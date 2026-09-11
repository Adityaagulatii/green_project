(ns tetris.displays.lease-test
  "The relay's fold: scenarios for every rule and boundary, and invariants
  over random logs, with the simulated scheduler writing expiry events."
  (:require [clojure.test :refer [deftest is testing use-fixtures]]
            [clojure.test.check.generators :as gen]
            [clojure.test.check.properties :as prop]
            [tetris.displays.caps :as caps]
            [tetris.displays.codec :as codec]
            [tetris.displays.lease :as lease]
            [tetris.displays.prop :refer [holds instrument-fixture]]
            [tetris.displays.specs :as ds]))

(use-fixtures :once instrument-fixture)

(def cfg (lease/config))
(def gb "green-building")
(def cga16 (codec/palette16 (caps/palette "cga")))

(defn open [ts c] {:event :open :ts ts :conn c})
(defn ctl [ts c msg] {:event :control :ts ts :conn c :msg msg})
(defn frm [ts c data] {:event :frame :ts ts :conn c :data data})
(defn bye [ts c] {:event :close :ts ts :conn c})
(defn view [ts c & [d]] (ctl ts c (cond-> {:op "view"} d (assoc :display d))))
(defn reserve [ts c & [m]] (ctl ts c (merge {:op "reserve" :name (str "src" c)} m)))

(defn out
  "What conn c was sent, in order: JSON maps, [:frame data], [:close code]."
  [{:keys [effects]} c]
  (into [] (comp cat (filter #(= c (:to %)))
                 (map (fn [f] (case (:fx f) :send (:msg f) :frame [:frame (:data f)] :close [:close (:code f)]))))
        effects))

(defn reasons [r c] (into [] (keep (fn [m] (when (and (map? m) (= "error" (:op m))) (:reason m)))) (out r c)))
(defn frames [r c] (into [] (keep (fn [m] (when (and (vector? m) (= :frame (first m))) (second m)))) (out r c)))

(defn play
  "Run events with the scheduler, firing only the timers due before the
  last event."
  ([events] (play cfg events))
  ([config events] (lease/run config events)))

(defn play-all
  "Run events, then let every pending timer fire."
  ([events] (play-all cfg events))
  ([config events] (lease/run config events (+ 10000000 (:ts (peek events))))))

(defn fill [n v] (vec (repeat n v)))

;; "a viewer (1) and a holder (2) on green-building", from ts 0
(def base [(open 0 1) (open 0 2) (view 0 1) (reserve 0 2)])

;; ------------------------------------------------------------ viewers

(deftest viewer-caps-and-lease
  (let [r (play [(open 0 1) (view 0 1)])]
    (is (= [{:op "caps" :display gb :w 9 :h 17 :fps 30 :format "pal16" :palette cga16}
            {:op "lease" :display gb :holder nil :expires nil}]
           (out r 1)))))

(deftest default-display
  (is (= "cga40" (:display (first (out (play (lease/config {:default "cga40"}) [(open 0 1) (view 0 1)]) 1))))
      "--default cga40: an omitted display resolves to it")
  (is (= [40 25] ((juxt :w :h) (first (out (play [(open 0 1) (view 0 1 "cga40")]) 1)))))
  (is (= ["bad-format"] (reasons (play [(open 0 1) (view 0 1 "nope")]) 1)))
  (is (= ["bad-format"] (reasons (play [(open 0 1) (view 0 1 "")]) 1)))
  (is (= (codec/palette16 (caps/palette "gb")) (get-in (lease/config) [:displays "gameboy" :palette]))
      "a 4-entry palette is announced as 16 through the level rule"))

(deftest viewer-cap-32
  (let [ev (into (vec (mapcat (fn [c] [(open 0 c) (view 0 c)]) (range 1 34))) [(view 1 1)])
        r (play ev)]
    (is (= ["caps" "lease"] (mapv :op (out r 32))) "the 32nd viewer is in")
    (is (= [[:close 1013]] (out r 33)) "the 33rd is closed with 1013")
    (is (= 32 (get-in (lease/view (:state r)) [:displays gb :viewers])))
    (is (= 4 (count (out r 1))) "an existing viewer may view again")))

;; ------------------------------------------------------------ reserve

(deftest reserve-granted-busy
  (let [r (play (conj base (reserve 5 3) (open 1 3)))]
    (is (= [{:op "granted" :lease "L1" :w 9 :h 17 :fps 30 :format "pal16" :palette cga16 :expires 300}]
           (out r 2)))
    (is (= {:op "lease" :display gb :holder "src2" :expires 300} (last (out r 1))))
    (is (= [] (reasons r 3)) "an unopened conn is a T2 anomaly, not an error"))
  (let [r (play [(open 0 1) (open 0 2) (reserve 0 1) (reserve 1000 2)])]
    (is (= [{:op "busy" :holder "src1" :expires 300}] (out r 2)))))

(deftest ttl-boundaries
  (let [exp (fn [m] (:expires (first (out (play [(open 0 1) (reserve 0 1 m)]) 1))))]
    (is (= 300 (exp {})) "absent: 300 s")
    (is (= 1 (exp {:ttl 1})))
    (is (= 900 (exp {:ttl 900})))
    (is (= 900 (exp {:ttl 901})) "clamped to 900")
    (is (= 2 (exp {:ttl 2.0})) "an integral float is an integer")
    (doseq [t [0 -1 1.5 "x" nil]]
      (is (= ["bad-format"] (reasons (play [(open 0 1) (reserve 0 1 {:ttl t})]) 1)) (pr-str t)))))

(deftest reserve-schema
  (is (= ["bad-format"] (reasons (play [(open 0 1) (ctl 0 1 {:op "reserve"})]) 1)) "name is required")
  (is (= ["bad-format"] (reasons (play [(open 0 1) (reserve 0 1 {:format "idx4"})]) 1)))
  (is (= "hex" (:format (first (out (play [(open 0 1) (reserve 0 1 {:format "hex"})]) 1)))))
  (is (= "rgb24" (:format (first (out (play [(open 0 1) (reserve 0 1 {:format "rgb24"})]) 1)))))
  (is (= ["unknown-op"] (reasons (play [(open 0 1) (ctl 0 1 {:op "frobnicate"})]) 1)))
  (is (= ["bad-format"] (reasons (play [(open 0 1) (ctl 0 1 {})]) 1)) "no op")
  (is (= ["bad-format"] (reasons (play [(open 0 1) (ctl 0 1 {:op 7})]) 1)) "op not a string")
  (is (= ["bad-format"] (reasons (play [(open 0 1) {:event :control :ts 0 :conn 1 :malformed true}]) 1))))

(deftest one-lease-per-connection
  (let [r (play (conj base (reserve 10 2 {:display "trs80"})))]
    (is (= {:op "lease" :display gb :holder nil :expires nil} (last (out r 1))) "the first is released")
    (is (= {gb :free "trs80" :held}
           (-> (lease/view (:state r)) :displays (select-keys [gb "trs80"]) (update-vals :state))))))

(deftest reserve-again-is-a-new-lease
  (let [r (play (conj base (reserve 10 2)))]
    (is (= ["L1" "L2"] (mapv :lease (out r 2))))))

;; ------------------------------------------------------------ frames

(deftest frames-fan-out
  (let [cells (mapv #(rem % 16) (range 153))
        r (play (conj base
                      (frm 100 2 cells)
                      (frm 200 2 (codec/encode-pal16 cells 7))
                      (frm 300 2 (codec/encode-hex 9 cells))
                      (frm 400 1 cells)))]
    (is (= [cells cells cells] (frames r 1)) "pal16, pal16 minus its prefix, hex as pal16")
    (is (= [] (reasons r 2)))
    (is (= ["not-holder"] (reasons r 1)) "a viewer's frame")))

(deftest frame-errors
  (let [r (play (conj base
                      (frm 100 2 (fill 152 0))
                      (frm 200 2 (fill 154 0))
                      (frm 300 2 (assoc (fill 153 0) 5 16))
                      (frm 400 2 (str "g" (subs (codec/encode-hex 9 (fill 153 0)) 1)))
                      (frm 500 2 "{not json")))]
    (is (= ["bad-frame-length" "bad-frame-length" "bad-format" "bad-format" "bad-frame-length"] (reasons r 2))
        "text reaching the fold as a frame is hex (the shell sends text starting with { as control)")
    (is (= [] (frames r 1)))))

(deftest rgb24-and-interop
  (let [cells (mapv #(if (even? %) 15 0) (range 153))
        rgb (vec (mapcat #(if (= 15 %) [255 255 255] [0 0 0]) cells))
        r (play [(open 0 1) (open 0 2) (view 0 1) (reserve 0 2 {:format "rgb24"})
                 (frm 100 2 rgb) (frm 200 2 (pop rgb)) (frm 300 2 cells)])]
    (is (= [cells] (frames r 1)) "quantized at the relay")
    (is (= ["bad-frame-length" "bad-frame-length"] (reasons r 2)) "an rgb24 lease takes no pal16"))
  (let [bits (mapv #(mod % 2) (range 153))
        r (play (conj base (frm 100 2 (codec/encode-blp 9 17 bits)) (frm 200 2 (codec/encode-blp 10 20 (fill 200 1)))))]
    (is (= [(mapv #(* 15 %) bits)] (frames r 1)) "BLP 0|1 -> 0|15")
    (is (= ["bad-frame-length"] (reasons r 2)) "a packet for another grid")))

(deftest fps-boundaries
  (let [rate (fn [config d gaps]
               (let [ts (reductions + 1000 gaps)
                     r (play config (into [(open 0 1) (open 0 2) (view 0 1 d) (reserve 0 2 {:display d})]
                                          (map (fn [t] (frm t 2 (fill (* (:w (caps/preset d)) (:h (caps/preset d))) 1))) ts)))]
                 [(count (frames r 1)) (reasons r 2)]))]
    (testing "30 fps (tolerance 20%): 26 ms is too fast, 27 is not"
      (is (= [1 ["rate"]] (rate cfg gb [26])))
      (is (= [2 []] (rate cfg gb [27]))))
    (testing "hub75 at 60 fps: 13 ms is too fast, 14 is not"
      (is (= [1 ["rate"]] (rate cfg "hub75" [13])))
      (is (= [2 []] (rate cfg "hub75" [14]))))
    (testing "exactly 60 fps with whole-ms timestamps is never dropped"
      (is (= [61 []] (rate cfg "hub75" (take 60 (cycle [17 17 16]))))))
    (testing "just above the limit: 62.5 fps sustained (16 ms) is held under 60"
      (let [[n errs] (rate cfg "hub75" (repeat 120 16))]
        (is (= 121 (+ n (count errs))))
        (is (pos? (count errs)))
        (is (<= n (inc (quot (* 120 16 60) 1000))) "no more than 60 fps allows over 1.92 s")
        (println (str "LEASE 62.5fps-at-60: accepted=" n " dropped=" (count errs)))))))

(deftest sequence-boundaries
  (let [s (fn [config seqs]
            (let [r (play config (into base (map-indexed (fn [i q] (frm (* 100 (inc i)) 2 (if q (codec/encode-pal16 (fill 153 1) q) (fill 153 1))))
                                                         seqs)))]
              (reasons r 2)))]
    (is (= [] (s cfg [0 0 1 65535])) "0, equal, up, 65535")
    (is (= ["rate"] (s cfg [5 3])) "lower is dropped")
    (is (= ["rate"] (s cfg [65535 0])) "the wrap 65535 -> 0: literal drops it (OPEN QUESTION)")
    (is (= ["rate" "rate"] (s cfg [65535 nil 0 1])) "no prefix: accepted, and the last stays 65535")
    (is (= [] (s (lease/config {:seq-rule :serial}) [65535 0 1])) "serial accepts the wrap")
    (is (= ["rate"] (s (lease/config {:seq-rule :serial}) [5 3])))))

;; ------------------------------------------------------------ renew, release, close, expiry

(deftest renew-release-close
  (is (= ["not-holder" "not-holder"] (reasons (play [(open 0 1) (ctl 0 1 {:op "renew"}) (ctl 0 1 {:op "release"})]) 1)))
  (let [r (play (conj base (ctl 1500 2 {:op "renew"})))]
    (is (= [] (reasons r 2)))
    (is (= {:op "lease" :display gb :holder "src2" :expires 302} (last (out r 1))) "a new whole second: announced"))
  (let [r (play (conj base (ctl 100 2 {:op "release"}) (reserve 200 1)))]
    (is (= [{:op "lease" :display gb :holder nil :expires nil}] (subvec (out r 1) 3 4)) "release: lease null, no black frame")
    (is (= "L2" (:lease (last (filter #(= "granted" (:op %)) (out r 1)))))))
  (let [r (play (conj base (bye 100 2)))]
    (is (= {:op "lease" :display gb :holder nil :expires nil} (last (out r 1))) "close: as release")
    (is (= [] (frames r 1)))))

(deftest expiry-is-an-event
  (let [r (play-all [(open 0 1) (open 0 2) (view 0 1) (reserve 1000 2 {:ttl 1})])
        exp (filter #(= :expire (:event %)) (:log r))]
    (is (= [{:event :expire :ts 2000 :display gb :lease "L1"}] exp) "the scheduler wrote it into the log")
    (is (= [{:op "lease" :display gb :holder nil :expires nil} [:frame (fill 153 0)]] (take-last 2 (out r 1)))
        "every viewer: lease null, then the black frame")
    (is (= [{:op "granted" :lease "L1" :w 9 :h 17 :fps 30 :format "pal16" :palette cga16 :expires 2}] (out r 2))
        "the holder is not told")
    (is (= :free (get-in (lease/view (:state r)) [:displays gb :state]))))
  (testing "renewed before its deadline: the early expiry is stale and re-arms"
    (let [r (play-all [(open 0 1) (open 0 2) (view 0 1) (reserve 1000 2 {:ttl 1}) (frm 1500 2 (fill 153 3))])]
      (is (= [2000 2500] (mapv :ts (filter #(= :expire (:event %)) (:log r)))))))
  (testing "hex fan-out: the black frame is hex text"
    (let [r (play-all (lease/config {:fanout "hex"}) [(open 0 1) (open 0 2) (view 0 1) (reserve 0 2 {:ttl 1})])]
      (is (= "hex" (:format (first (out r 1)))))
      (is (= [:frame (codec/encode-hex 9 (fill 153 0))] (last (out r 1)))))))

(deftest ladder-gates-and-view
  (let [st (:state (lease/fold cfg [{:event :bogus :ts 1} (frm 2 9 [0]) (open 5 1) (open 3 2)]))]
    (is (= [{:inv "schema" :event :bogus :ts 1} {:inv "T2" :event :frame :ts 2 :conn 9}
            {:inv "I6" :event :open :ts 3 :conn 2}]
           (:anomalies st)))
    (is (= #{1 2} (set (keys (:conns st)))) "an I6 entry is recorded, then applied"))
  (let [st (:state (lease/fold cfg [(open 0 1) (reserve 0 1 {:ttl 1}) (open 5000 2)]))]
    (is (= [{:inv "X1" :display gb :lease "L1" :due 1000 :watermark 5000}] (:anomalies (lease/view st)))
        "a deadline behind the watermark with no expiry event written")))

;; ------------------------------------------------------------ properties

(def tcfg (lease/config {:max-viewers 2}))

(defn- trace
  "A random external log played with the scheduler: [[state event fx state'] ...] and the run."
  [log]
  (let [r (lease/run tcfg log (+ 2000000 (:ts (peek log))))
        states (reductions (fn [s e] (first (lease/step s e))) (lease/init tcfg) (:log r))]
    [(map vector states (:log r) (:effects r) (rest states)) r]))

(defn- check-trace [pred]
  (prop/for-all [log (lease/log-gen)]
    (let [[steps _] (trace log)] (every? pred steps))))

(deftest l1-holders
  (holds "lease/L1: one holder per display, an open connection, holding at most one"
         (check-trace (fn [[_ _ _ s]]
                        (and (every? (fn [[d l]] (and (contains? (:conns s) (:holder l))
                                                      (= d (get-in s [:conns (:holder l) :holding]))))
                                     (:leases s))
                             (= (count (:leases s)) (count (distinct (map :holder (vals (:leases s)))))))))))

(deftest l2-reasons
  (holds "lease/L2: every error reason is one of the five"
         (check-trace (fn [[_ _ fx _]]
                        (every? #(or (not= "error" (get-in % [:msg :op])) (ds/reasons (get-in % [:msg :reason]))) fx)))))

(deftest l3-viewer-cap
  (holds "lease/L3: viewers per display <= the cap"
         (check-trace (fn [[_ _ _ s]] (every? #(<= (count (lease/viewers s %)) 2) (keys (:displays s)))))))

(deftest l4-ttl
  (holds "lease/L4: every granted and lease expires is at most 900 s ahead"
         (check-trace (fn [[_ e fx _]]
                        (every? #(or (not (#{"granted" "lease"} (get-in % [:msg :op])))
                                     (nil? (get-in % [:msg :expires]))
                                     (<= (get-in % [:msg :expires]) (lease/expires-s (+ (:ts e) 900000))))
                                fx)))))

(deftest l5-expiry
  (holds "lease/L5: a live expiry sends each viewer lease null then a black frame, and nothing else"
         (check-trace (fn [[s e fx _]]
                        (let [l (get-in s [:leases (:display e)])]
                          (if (and (= :expire (:event e)) l (= (:lease e) (:id l)) (>= (:ts e) (:expires l)))
                            (let [d (:display e)
                                  n (* (get-in s [:displays d :w]) (get-in s [:displays d :h]))]
                              (= fx (vec (mapcat (fn [v] [{:fx :send :to v :msg {:op "lease" :display d :holder nil :expires nil}}
                                                          {:fx :frame :to v :data (fill n 0)}])
                                                 (lease/viewers s d)))))
                            true))))))

(deftest l6-fan-out
  (holds "lease/L6: frames reach viewers only from the holder, as w*h cells 0..15; a non-holder gets not-holder"
         (check-trace (fn [[s e fx _]]
                        (let [frames (filter #(= :frame (:fx %)) fx)
                              d (when (= :frame (:event e)) (lease/holding s (:conn e)))]
                          (cond
                            (and (= :frame (:event e)) (not (contains? (:conns s) (:conn e))))
                            (empty? fx) ; a closed or unopened connection: a T2 anomaly, no effects

                            (and (= :frame (:event e)) (nil? d))
                            (= fx [{:fx :send :to (:conn e) :msg {:op "error" :reason "not-holder"}}])

                            (= :frame (:event e))
                            (let [{:keys [w h]} (get-in s [:displays d])]
                              (and (every? #(and (= (* w h) (count (:data %))) (every? (fn [c] (<= 0 c 15)) (:data %))) frames)
                                   (every? #(contains? (set (lease/viewers s d)) (:to %)) frames)))

                            (= :expire (:event e)) true
                            :else (empty? frames)))))))

(defn- accepted
  "[lease-id ts seq] of every accepted frame in a trace."
  [steps]
  (for [[s e fx _] steps
        :let [d (and (= :frame (:event e)) (lease/holding s (:conn e)))]
        :when (and d (not-any? #(and (= (:conn e) (:to %)) (= "error" (get-in % [:msg :op]))) fx))
        :let [l (get-in s [:leases d])]]
    [(:id l) (:ts e) (:seq (lease/decode-frame (get-in s [:displays d]) (:format l) (:data e))) (get-in s [:displays d :fps])]))

(deftest l7-rate
  (holds "lease/L7: accepted frames i<j of a lease: (t_j - t_i)*fps >= (j-i)*1000 - tolerance"
         (prop/for-all [log (lease/log-gen)]
           (let [[steps _] (trace log)]
             (every? (fn [[_ fs]]
                       (let [fs (vec fs)]
                         (every? true? (for [i (range (count fs)) j (range (inc i) (count fs))
                                             :let [[_ ti _ fps] (fs i) [_ tj] (fs j)]]
                                         (>= (* (- tj ti) fps) (- (* (- j i) 1000) lease/default-rate-tolerance))))))
                     (group-by first (accepted steps)))))))

(deftest l8-replay
  (holds "lease/L8: the log is the source of truth: fold(log) reproduces the run"
         (prop/for-all [log (lease/log-gen)]
           (let [[_ r] (trace log)
                 f (lease/fold tcfg (:log r))]
             (and (= (:state r) (:state f)) (= (:effects r) (:effects f)))))))

(deftest l9-sequence
  (holds "lease/L9: within a lease, accepted prefixed frames never go down (literal rule)"
         (prop/for-all [log (lease/log-gen)]
           (let [[steps _] (trace log)]
             (every? (fn [[_ fs]] (let [qs (keep #(nth % 2) fs)] (or (empty? qs) (apply <= qs))))
                     (group-by first (accepted steps)))))))
