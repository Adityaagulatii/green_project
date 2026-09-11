(ns tetris.displays.fixtures-test
  "Cross-check against the displays-contract fixtures (contract/fixtures/*.json,
  written by contract/gen_fixtures.py from the Python reference,
  contract/display_contract.py). Every case is replayed through this repo's
  codec, core and lease, the way another language reads the files.

  The directory is $DISPLAYS_FIXTURES, else ../contract/fixtures (where the
  contract workstream writes them on contrib/displays). When it is absent
  the tests print so and pass. `bb fixtures [dir]` prints one FIXTURE line
  per file: cases, passed, and the names that disagree."
  (:require [cheshire.core :as json]
            [clojure.java.io :as io]
            [clojure.spec.alpha :as s]
            [clojure.string :as str]
            [clojure.test :refer [deftest is]]
            [tetris.displays.caps :as caps]
            [tetris.displays.codec :as codec]
            [tetris.displays.core :as core]
            [tetris.displays.lease :as lease]
            [tetris.displays.relay :as relay]))

(def ^:dynamic *dir* nil)

(defn fixtures-dir []
  (some (fn [p] (when (and p (.isDirectory (io/file p))) p))
        [*dir* (System/getenv "DISPLAYS_FIXTURES") "../contract/fixtures"]))

(defn- load-fixture [fname]
  (when-let [d (fixtures-dir)]
    (let [f (io/file d (str fname ".json"))]
      (when (.exists f) (json/parse-string (slurp f) true)))))

;; ------------------------------------------------------------ JSON shapes

(defn- rle [runs] (vec (mapcat (fn [[v n]] (repeat n v)) runs)))

(defn frame-of
  "A fixture frame ({:bytes} {:bytes_rle} {:text} {:text_rle}) -> octets or text."
  [m]
  (cond
    (contains? m :bytes) (:bytes m)
    (contains? m :bytes_rle) (rle (:bytes_rle m))
    (contains? m :text) (:text m)
    (contains? m :text_rle) (apply str (rle (:text_rle m)))
    :else ::none))

(defn- cells-of [j] (if (contains? j :cells_rle) (rle (:cells_rle j)) (some-> (:cells j) vec)))

(defn ->state
  "A reference state (JSON) in this core's shape."
  [j]
  {:w (:w j) :h (:h j) :fixed? (boolean (:fixed j)) :format (keyword (:format j))
   :palette (let [p (:palette j)] (if (string? p) (keyword p) (vec p)))
   :fps (:fps j) :status (keyword (:status j)) :holder (:holder j) :expires (:expires j)
   :seq (:seq j) :dropped (:dropped j) :error (:error j) :cells (cells-of j)})

(defn ->event
  "A reference event (JSON) as this core's event: local events carry
  \"event\", control messages \"op\"; anything else passes through."
  [e]
  (if (and (map? e) (contains? e :event))
    (case (:event e)
      "frame" (let [d (frame-of e)] (cond-> {:op :frame} (not= d ::none) (assoc :data d)))
      "open" {:op :open}
      "close" {:op :close}
      "tick" (cond-> {:op :tick} (contains? e :now) (assoc :now (:now e)))
      {:op [:unknown-local (:event e)]})
    e))

(defn- pal-rgb [p]
  (mapv codec/hex->rgb (codec/palette16 (if (string? p) (caps/palette p) p))))

;; ------------------------------------------------------------ per-file checks

(defn fold-case
  "nil when the core's states (and dirty cells) equal the case's, else the first difference."
  [c]
  (let [evs (mapv ->event (:events c))
        states (vec (rest (reductions core/reduce-event (->state (:initial c)) evs)))
        prevs (into [(->state (:initial c))] states)]
    (first
     (keep-indexed
      (fn [i x]
        (let [mine (states i)
              want (->state x)
              dirt (get (:dirty c) i)]
          (cond
            (not= mine want)
            {:event i :in (get (:events c) i)
             :diff (into {} (keep (fn [k] (when (not= (k mine) (k want)) [k [(k mine) (k want)]]))) (keys want))}
            (and dirt (not= (mapv vec dirt) (core/dirty mine (prevs i))))
            {:event i :dirty [(count (core/dirty mine (prevs i))) (count dirt)]})))
      (:states c)))))

(defn frame-case [{:keys [w h format palette expect] :as c}]
  (let [data (frame-of c)
        r (case format
            "pal16" (codec/decode-pal16 w h data)
            "hex" (codec/decode-hex w h data)
            "rgb24" (codec/decode-rgb24 w h (pal-rgb (or palette "cga")) data))
        got (if (:cells r) {:ok true :cells (:cells r) :seq (:seq r)} {:ok false :reason (:error r)})
        want (if (:ok expect)
               {:ok true :cells (cells-of expect) :seq (:seq expect)}
               {:ok false :reason (:reason expect)})]
    (when (not= got want)
      {:got (update got :cells #(some-> % count)) :want (update want :cells #(some-> % count))})))

(defn equivalence-case [{:keys [w h] :as c}]
  (let [p (codec/decode-pal16 w h (frame-of (:pal16 c)))
        x (codec/decode-hex w h (frame-of (:hex c)))]
    (when-not (= (cells-of c) (:cells p) (:cells x))
      {:pal16 (or (:error p) :cells-differ) :hex (or (:error x) :cells-differ)})))

(defn grid-case [{:keys [w h error]}]
  (let [ok (core/grid-ok? w h)
        init-reason (-> (core/init {:w w :h h}) :error :reason)]
    (when-not (and (= ok (nil? error)) (or (nil? error) (= "bad-cells" error) (= error init-reason)))
      {:grid-ok? ok :init init-reason :want error})))

(defn length-case [{:keys [w h pal16 hex rgb24]}]
  (let [zeros #(vec (repeat % 0))
        n (* w h)
        pal (pal-rgb "cga")
        oks [(codec/decode-pal16 w h (zeros (first pal16))) (codec/decode-pal16 w h (zeros (second pal16)))
             (codec/decode-hex w h (codec/encode-hex w (zeros n))) (codec/decode-hex w h (str (codec/encode-hex w (zeros n)) "\n"))
             (codec/decode-rgb24 w h pal (zeros (first rgb24))) (codec/decode-rgb24 w h pal (zeros (second rgb24)))]]
    (when-not (and (every? :cells oks)
                   (= [(count (codec/encode-hex w (zeros n))) (inc (count (codec/encode-hex w (zeros n))))] hex)
                   (every? #(= "bad-frame-length" (:error %))
                           [(codec/decode-pal16 w h (zeros (dec (first pal16))))
                            (codec/decode-pal16 w h (zeros (inc (first pal16))))
                            (codec/decode-pal16 w h (zeros (inc (second pal16))))]))
      {:lengths [pal16 hex rgb24]})))

(defn interop-case [{:keys [expect] :as c}]
  (let [p (codec/parse-interop (frame-of c))]
    (if (:ok expect)
      (let [d (caps/display-for (:w p) (:h p))
            cells (when (and d (not (:error p)))
                    (codec/interop-cells p (pal-rgb (:palette (caps/preset d)))))
            got {:kind (some-> (:kind p) name) :w (:w p) :h (:h p) :channels (:channels p)
                 :maxval (:maxval p) :display d :cells (or cells [])}
            want (-> (select-keys expect [:kind :w :h :channels :maxval :display])
                     (assoc :cells (or (cells-of expect) [])))]
        (when (not= got want) {:got (update got :cells count) :want (update want :cells count) :error (:error p)}))
      (when (not= (:reason expect) (:error p)) {:got (:error p) :want (:reason expect)}))))

(defn levels-case
  "A palette size outside 2..16 must be outside codec/level's :args domain;
  otherwise the table (and the idx 0/1/15 boundaries and the named
  palette's palette16, where the case gives them) must match."
  [{:keys [n levels palette palette16 error] :as c}]
  (if error
    (when (s/valid? (:args (s/get-spec `codec/level)) [1 n]) {:domain-admits n})
    (let [ls (mapv #(codec/level % n) (range 16))]
      (when-not (and (= levels ls)
                     (every? (fn [[k i]] (or (not (contains? c k)) (= (get c k) (ls i))))
                             [[:idx0 0] [:idx1 1] [:idx15 15]])
                     (or (nil? palette) (= palette16 (codec/palette16 (caps/palette palette)))))
        {:got ls}))))

(defn- relay-reason
  "The error reason this relay's fold answers `text` with, from a fresh
  connection that holds nothing (nil when it answers no error)."
  [text]
  (let [r (lease/run (lease/config) [{:event :open :ts 0 :conn 1} (assoc (relay/text->event text) :ts 0 :conn 1)])]
    (some (fn [f] (when (and (= 1 (:to f)) (= "error" (get-in f [:msg :op]))) (get-in f [:msg :reason])))
          (apply concat (:effects r)))))

(def ^:private to-relay #{"view" "reserve" "renew" "release"})

(defn- my-valid? [m]
  (let [op (when (map? m) (:op m))]
    (cond
      (contains? to-relay op) (lease/message-ok? m)
      (= "caps" op) (core/caps-ok? m)
      (= "lease" op) (core/lease-ok? m)
      (= "error" op) (contains? #{"not-holder" "bad-frame-length" "rate" "bad-format" "unknown-op"} (:reason m))
      :else ::n-a)))

(def known
  "Disagreements with the fixtures that are the fixtures' (reported to the
  contract workstream), not ours: case -> why."
  {:non-object-message
   (str "a JSON value that is not an object ([], \"view\") is sent as text that does not start "
        "with {, so by the spec's first-character rule it is a hex frame: not-holder from a "
        "non-holder (the reference relay, demo/relay.py _frame, answers the same); the fixture "
        "expects bad-format, as if it were parsed as control")})

(defn message-case [{:keys [message valid relay]}]
  (let [v (my-valid? message)
        r (relay-reason (json/generate-string message))
        op (when (map? message) (:op message))]
    (cond
      (and (not (map? message)) (= [r relay] ["not-holder" "bad-format"])) {:known :non-object-message}
      (and (not= v ::n-a) (not= v valid)) {:valid [v valid]}
      (some? relay) (when (not= r relay) {:relay [r relay]})
      (contains? to-relay op) (when (contains? #{"bad-format" "unknown-op"} r) {:relay [r nil]})
      :else nil)))

(defn raw-case [{:keys [text relay]}]
  (let [r (relay-reason text)] (when (not= r relay) {:relay [r relay]})))

(defn quantize-case [{:keys [palette rgb index]}]
  (let [i (codec/nearest (pal-rgb palette) rgb)] (when (not= i index) {:got i})))

(defn sequence-case [{:keys [rule seqs accepted reasons]}]
  (let [acc (loop [last nil qs seqs out []]
              (if (empty? qs)
                out
                (let [q (first qs)
                      drop? (lease/reordered? (keyword rule) last q)]
                  (recur (if (or drop? (nil? q)) last q) (rest qs) (conj out (not drop?))))))]
    (when-not (and (= accepted acc) (= reasons (mapv #(when-not % "rate") acc))) {:got acc})))

;; ------------------------------------------------------------ the runs

(def files
  "fixture file -> [the key of its cases, the check]"
  {"caps" [:cases fold-case] "fold" [:cases fold-case] "expiry" [:cases fold-case]
   "frames" [:cases frame-case] "equivalence" [:cases equivalence-case]
   "grid" [:cases grid-case] "grid.lengths" [:lengths length-case]
   "interop" [:cases interop-case] "levels" [:cases levels-case]
   "messages" [:cases message-case] "messages.raw" [:raw raw-case]
   "quantize" [:cases quantize-case] "sequence" [:cases sequence-case]})

(defn check-file
  "{:file :cases :passed :failed [[name difference] ...]}, or nil when absent."
  [k]
  (let [[fname] (str/split k #"\.")
        [ck f] (files k)]
    (when-let [fx (load-fixture fname)]
      (let [cases (get fx ck)
            diffs (into [] (keep-indexed (fn [i c] (when-let [d (f c)] [(or (:name c) (:note c) i) d]))) cases)
            kn (filterv #(:known (second %)) diffs)
            bad (filterv #(not (:known (second %))) diffs)]
        {:file k :cases (count cases) :passed (- (count cases) (count diffs)) :known kn :failed bad}))))

(defn- report [{:keys [file cases passed failed] kn :known}]
  (println (str "FIXTURE " file " cases=" cases " passed=" passed " known=" (count kn)
                " failed=" (count failed)))
  (binding [*print-length* 8 *print-level* 4]
    (doseq [[n d] kn] (println "    KNOWN" (pr-str n) (get known (:known d))))
    (doseq [[n d] (take 12 failed)] (println "   " (pr-str n) (pr-str d)))))

(defn- check-and-assert [k]
  (if-let [r (check-file k)]
    (do (report r)
        (is (empty? (:failed r)) (str k ": " (count (:failed r)) " of " (:cases r) " disagree")))
    (println (str "FIXTURE " k " absent (no fixtures directory)"))))

(deftest fixtures-caps (check-and-assert "caps"))
(deftest fixtures-fold (check-and-assert "fold"))
(deftest fixtures-expiry (check-and-assert "expiry"))
(deftest fixtures-frames (check-and-assert "frames"))
(deftest fixtures-equivalence (check-and-assert "equivalence"))
(deftest fixtures-grid (check-and-assert "grid") (check-and-assert "grid.lengths"))
(deftest fixtures-interop (check-and-assert "interop"))
(deftest fixtures-levels (check-and-assert "levels"))
(deftest fixtures-messages (check-and-assert "messages") (check-and-assert "messages.raw"))
(deftest fixtures-quantize (check-and-assert "quantize"))
(deftest fixtures-sequence (check-and-assert "sequence"))

(defn -main
  "bb fixtures [dir]"
  [& [dir]]
  (binding [*dir* dir]
    (println "fixtures:" (or (fixtures-dir) "none found"))
    (let [{:keys [fail error]} (clojure.test/run-tests 'tetris.displays.fixtures-test)]
      (when (pos? (+ fail error)) (throw (ex-info "fixtures disagree" {:fail fail :error error}))))))
