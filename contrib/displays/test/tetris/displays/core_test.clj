(ns tetris.displays.core-test
  "The sink's fold: the Reduction contract table row by row, the section 13
  conformance list, boundaries, and the six invariants as properties."
  (:require [clojure.test :refer [deftest is testing use-fixtures]]
            [clojure.test.check.generators :as gen]
            [clojure.test.check.properties :as prop]
            [tetris.displays.caps :as caps]
            [tetris.displays.codec :as codec]
            [tetris.displays.core :as core]
            [tetris.displays.prop :refer [holds instrument-fixture]]))

(use-fixtures :once instrument-fixture)

(def cga16 (codec/palette16 (caps/palette "cga")))

(defn caps-msg
  ([w h] (caps-msg w h "pal16"))
  ([w h fmt] {:op "caps" :display "x" :w w :h h :fps 30 :format fmt :palette cga16}))

(defn frame [data] {:op :frame :data data})
(defn run [s & evs] (core/reduce-events s evs))

(def s0 (core/init))

;; ------------------------------------------------------------ init

(deftest init-defaults-and-params
  (testing "green-building, this repo's default"
    (is (= {:w 9 :h 17 :fixed? false :format :pal16 :palette :cga :fps 30 :status :connecting
            :holder nil :expires nil :seq 0 :dropped 0 :error nil}
           (dissoc s0 :cells)))
    (is (= (vec (repeat 153 0)) (:cells s0))))
  (testing "grid boundaries: 1 and 256 accepted, 0 and 257 rejected"
    (is (= [1 1 true :connecting] ((juxt :w :h :fixed? :status) (core/init {:w 1 :h 1}))))
    (is (= [256 256 65536] ((juxt :w :h (comp count :cells)) (core/init {:w 256 :h 256}))))
    (is (= [9 17] ((juxt :w :h) (core/init {:w 9.0 :h 17.0}))) "integral floats count")
    (doseq [[p reason] [[{:w 0} "bad-w"] [{:w 257} "bad-w"] [{:h 0} "bad-h"] [{:h 257} "bad-h"]
                        [{:w "9"} "bad-w"] [{:d "nope"} "bad-preset"] [{:fps 61} "bad-fps"] [{:fps 0} "bad-fps"]]]
      (let [s (core/init p)]
        (is (= [:error reason 9 17 false] [(:status s) (-> s :error :reason) (:w s) (:h s) (:fixed? s)]) (pr-str p)))))
  (testing "fps lowers the preset's rate, never raises it"
    (is (= 30 (:fps (core/init {:fps 60}))))
    (is (= 10 (:fps (core/init {:fps 10}))))
    (is (= 60 (:fps (core/init {:d "hub75" :fps 60}))))))

;; ------------------------------------------------------------ the table

(deftest reduction-table
  (testing ":open"
    (is (= :connecting (:status (run s0 {:op :close} {:op :open})))))
  (testing "caps: fps, format, palette, w and h; cells reallocated to 0"
    (let [s (run s0 (frame (vec (repeat 153 3))) (caps-msg 10 20 "hex"))]
      (is (= [10 20 30 :hex cga16 (vec (repeat 200 0)) 0]
             ((juxt :w :h :fps :format :palette :cells :dropped) s))))
    (is (= (vec (repeat 153 0)) (:cells (run s0 (frame (vec (repeat 153 3))) (caps-msg 9 17))))
        "same grid: still reallocated"))
  (testing "caps against a fixed grid"
    (let [fixed (core/init {:w 9 :h 17})
          s (run fixed (caps-msg 10 20))]
      (is (= [:error 9 17 {:reason "bad-src" :caps {:w 10 :h 20} :fixed {:w 9 :h 17}}]
             ((juxt :status :w :h :error) s)))
      (is (= :connecting (:status (run fixed (caps-msg 9 17)))))))
  (testing "lease"
    (let [s (run s0 {:op "lease" :display "x" :holder "emacs@minibos" :expires 100})]
      (is (= ["emacs@minibos" 100 :connecting] ((juxt :holder :expires :status) s)))
      (is (= [nil nil :idle] ((juxt :holder :expires :status)
                              (run s {:op "lease" :display "x" :holder nil :expires nil}))))))
  (testing ":frame: format and length must match; else dropped, state unchanged"
    (let [cells (vec (range 9 162))
          cells (mapv #(rem % 16) cells)
          s (run s0 (frame cells))]
      (is (= [cells 1 :live 0] ((juxt :cells :seq :status :dropped) s)))
      (is (= (update s0 :dropped inc) (run s0 (frame (codec/encode-hex 9 cells))))
          "a hex frame on a pal16 display is dropped")
      (is (= cells (:cells (run s0 (caps-msg 9 17 "hex") (frame (codec/encode-hex 9 cells))))))))
  (testing "error: recorded, status unchanged; an unknown reason is malformed"
    (is (= [{:reason "rate"} :connecting] ((juxt :error :status) (run s0 {:op "error" :reason "rate"}))))
    (is (= 1 (:dropped (run s0 {:op "error" :reason "nope"})))))
  (testing ":close keeps the cells"
    (let [s (run s0 (frame (vec (repeat 153 5))) {:op :close})]
      (is (= [:closed (vec (repeat 153 5))] ((juxt :status :cells) s)))))
  (testing ":tick: idle once expires <= now"
    (let [s (run s0 {:op "lease" :display "x" :holder "h" :expires 100} (frame (vec (repeat 153 1))))]
      (is (= :live (:status (run s {:op :tick :now 99}))))
      (is (= :idle (:status (run s {:op :tick :now 100}))))
      (is (= :idle (:status (run s {:op :tick :now 101}))))
      (is (= 1 (:dropped (run s {:op :tick}))))
      (is (= :connecting (:status (run s0 {:op :tick :now 1e12}))) "no expires, no change")))
  (testing "unknown input: unchanged but dropped"
    (doseq [e [nil 42 "caps" {} {:op "busy"} {:op "granted"} {:op :frob} [1 2]]]
      (is (= (update s0 :dropped inc) (core/reduce-event s0 e)) (pr-str e)))))

(deftest malformed-control-is-dropped
  (testing "caps boundaries (schema): grid 1 and 256 ok, 0 and 257 not; fps 60 ok, 61 not"
    (is (= [1 1] ((juxt :w :h) (run s0 (caps-msg 1 1)))))
    (is (= 65536 (count (:cells (run s0 (caps-msg 256 256))))))
    (doseq [m [(caps-msg 0 1) (caps-msg 257 1) (caps-msg 1 257) (caps-msg 1 0)
               (assoc (caps-msg 9 17) :fps 61) (assoc (caps-msg 9 17) :fps 0)
               (assoc (caps-msg 9 17) :format "rgb24") (update (caps-msg 9 17) :palette pop)
               (assoc (caps-msg 9 17) :palette "cga") (dissoc (caps-msg 9 17) :display)
               (dissoc (caps-msg 9 17) :fps)]]
      (is (= (update s0 :dropped inc) (core/reduce-event s0 m)) (pr-str (dissoc m :palette))))
    (is (= 60 (:fps (run s0 (assoc (caps-msg 64 32) :fps 60))))))
  (testing "lease (schema): display, holder and expires required"
    (is (= 1 (:dropped (run s0 {:op "lease" :display "x" :holder 7 :expires nil}))))
    (is (= 1 (:dropped (run s0 {:op "lease" :display "x" :holder nil :expires -1}))))
    (is (= 1 (:dropped (run s0 {:op "lease" :holder nil})))
        "OPEN QUESTION: the spec's own shorthand expiry message fails the schema; the reference drops it too")))

;; ------------------------------------------------------------ section 13 fixtures

(deftest conformance-list
  (doseq [d caps/display-names
          :let [{:keys [w h]} (caps/preset d)
                caps (caps-msg w h)
                s (run s0 caps)
                cells (mapv #(rem % 16) (range (* w h)))]]
    (testing d
      (is (= (* w h) (count (:cells s))) "caps -> cells length")
      (is (= cells (:cells (run s (frame cells)))) "a valid pal16 frame")
      (is (= cells (:cells (run s0 (caps-msg w h "hex") (frame (codec/encode-hex w cells))))) "a valid hex frame")
      (doseq [bad [(pop cells) (conj cells 0)]]
        (is (= (update s :dropped inc) (run s (frame bad))) "pal16 off by one"))
      (let [hs (run s0 (caps-msg w h "hex"))
            hex (codec/encode-hex w cells)]
        (doseq [bad [(subs hex 1) (str hex "0") (str "g" (subs hex 1))]]
          (is (= (update hs :dropped inc) (run hs (frame bad))) "hex off by one, and a g")))
      (is (= (update s :dropped inc) (run s (frame (assoc cells 0 16)))) "a byte of 16")))
  (testing "the same indices as pal16 and as hex decode to equal cells"
    (let [cells (mapv #(rem (* 7 %) 16) (range 153))]
      (is (= (:cells (run s0 (frame cells)))
             (:cells (run s0 (caps-msg 9 17 "hex") (frame (codec/encode-hex 9 cells))))))))
  (testing "the expiry sequence: lease holder null, then the black frame"
    (let [s (run s0 {:op "lease" :display "x" :holder "h" :expires 100} (frame (vec (repeat 153 9))))
          s1 (run s {:op "lease" :display "x" :holder nil :expires nil})
          s2 (run s1 (frame (vec (repeat 153 0))))]
      (is (= :idle (:status s1)))
      (is (= [(vec (repeat 153 0)) 2 nil] ((juxt :cells :seq :holder) s2)))
      (is (= :live (:status s2))
          "OPEN QUESTION: read literally, the table's valid-frame row makes the black frame :live again")
      (is (= 153 (count (core/dirty s2 s1))) "the DOM repaints every cell"))))

(deftest dirty-projection
  (let [s (run s0 (frame (vec (repeat 153 1))))
        s' (run s (frame (assoc (vec (repeat 153 1)) 10 7)))]
    (is (= [] (core/dirty s s)))
    (is (= [[1 1 7]] (core/dirty s' s)))
    (is (= 153 (count (core/dirty s nil))))
    (is (= 200 (count (core/dirty (run s (caps-msg 10 20)) s))))))

;; ------------------------------------------------------------ invariants

(defn- trace-gen
  "[initial-state events states]: a random log from a random preset."
  []
  (gen/fmap (fn [[d evs]]
              (let [s (core/init {:d d})]
                [s evs (vec (reductions core/reduce-event s evs))]))
            (gen/tuple (gen/elements ["green-building" "trs80" "blinkenlights" "hub75" "cga40"])
                       (gen/vector (core/event-gen) 0 30))))

(deftest invariant-1-cells-count
  (holds "core/I1: (count cells) = w*h on every reduced state"
         (prop/for-all [[_ _ states] (trace-gen)]
           (every? #(= (count (:cells %)) (* (:w %) (:h %))) states))))

(deftest invariant-2-cells-range
  (holds "core/I2: every cell is an integer in 0..15"
         (prop/for-all [[_ _ states] (trace-gen)]
           (every? (fn [s] (every? #(and (int? %) (<= 0 % 15)) (:cells s))) states))))

(deftest invariant-3-seq-monotone
  (holds "core/I3: :seq never decreases, and rises by 1 only on an accepted :frame"
         (prop/for-all [[_ evs states] (trace-gen)]
           (every? (fn [[s e s']]
                     (let [d (- (:seq s') (:seq s))]
                       (and (<= 0 d 1)
                            (or (zero? d) (and (= :frame (:op e)) (= (:dropped s) (:dropped s')))))))
                   (map vector states evs (rest states))))))

(def bad-faults
  "Faults that make a frame invalid in the state's own format."
  {:pal16 [:short :long :seq-short :seq-long :byte16 :byte255 :crlf :other-carrier]
   :hex [:short :long :seq-short :seq-long :byte16 :byte255 :crlf :other-carrier]})

(deftest invariant-4-bad-frames
  (holds "core/I4: a frame of the wrong length or with an out-of-range cell never changes :cells"
         (prop/for-all [[s f] (gen/bind (trace-gen)
                                        (fn [[_ _ states]]
                                          (let [s (peek states)
                                                n (* (:w s) (:h s))]
                                            (gen/fmap (fn [[xs fault sq]]
                                                        [s {:op :frame
                                                            :data (core/frame-data (:w s) (vec (take n (cycle xs)))
                                                                                   (:format s) fault sq)}])
                                                      (gen/tuple (gen/vector (gen/choose 0 15) 1 8)
                                                                 (gen/elements (bad-faults (:format s)))
                                                                 (gen/elements [0 1 65535]))))))]
           (let [s' (core/reduce-event s f)]
             (and (= (:cells s) (:cells s')) (= (update s :dropped inc) s'))))))

(deftest invariant-5-totality
  (holds "core/I5: reduce-event is total; unknown input counts as dropped"
         (prop/for-all [s (gen/fmap peek (gen/fmap #(nth % 2) (trace-gen)))
                        evs (gen/vector (gen/one-of [gen/any gen/any-printable
                                                     (gen/fmap (fn [op] {:op op}) gen/any)])
                                        1 10)]
           (every? (fn [e]
                     (let [s' (core/reduce-event s e)
                           known? (and (map? e) (contains? #{:open :close :tick :frame "caps" "lease" "error"} (:op e)))]
                       (and (map? s') (= (count (:cells s')) (* (:w s') (:h s')))
                            (or known? (= s' (update s :dropped inc))))))
                   evs))))

(deftest invariant-6-format-symmetry
  (holds "core/I6: the hex and pal16 encodings of the same indices decode to the same :cells"
         (prop/for-all [[w h] (gen/elements core/boundary-grids)
                        xs (gen/vector (gen/choose 0 15) 1 8)
                        sq (gen/elements [0 65535])]
           (let [cells (vec (take (* w h) (cycle xs)))
                 via (fn [fmt data] (:cells (run s0 (caps-msg w h fmt) (frame data))))]
             (= cells
                (via "pal16" cells)
                (via "pal16" (codec/encode-pal16 cells sq))
                (via "hex" (codec/encode-hex w cells))
                (via "hex" (str (codec/encode-hex w cells) "\n"))
                (:cells (codec/decode w h (codec/encode-hex w cells))))))))

(deftest dirty-property
  (holds "core/dirty: applied to prev it gives state (same grid); every cell otherwise"
         (prop/for-all [[_ _ states] (trace-gen)]
           (every? (fn [[p s]]
                     (let [ds (core/dirty s p)]
                       (if (= [(:w s) (:h s)] [(:w p) (:h p)])
                         (= (:cells s) (reduce (fn [cs [x y c]] (assoc cs (+ x (* y (:w s))) c)) (:cells p) ds))
                         (= (count ds) (count (:cells s))))))
                   (map vector states (rest states))))))
