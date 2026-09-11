(ns tetris.displays.adapt-test
  "SPEC 17x9 frames on every preset: colours, the loss policies, the report."
  (:require [clojure.spec.alpha :as s]
            [clojure.test :refer [deftest is testing use-fixtures]]
            [clojure.test.check.generators :as gen]
            [clojure.test.check.properties :as prop]
            [tetris.displays.adapt :as adapt]
            [tetris.displays.caps :as caps]
            [tetris.displays.prop :refer [holds instrument-fixture]]
            [tetris.displays.specs :as ds]))

(use-fixtures :once instrument-fixture)

(def rgb-of (into {} ds/spec-palette))
(def codes (mapv first ds/spec-palette))

(defn frame-of [f] (vec (for [y (range 17)] (vec (for [x (range 9)] (rgb-of (f x y)))))))

(def pattern (frame-of (fn [x y] (codes (mod (+ x (* 3 y)) 10)))))
(def lit (frame-of (constantly "W")))

(defn at [{:keys [w cells]} x y] (cells (+ x (* y w))))

(deftest colours
  (is (= {"." 0 "G" 8 "I" 11 "J" 1 "L" 6 "O" 14 "S" 2 "T" 5 "W" 15 "Z" 4} (adapt/spec-colors "green-building")))
  (is (= 0 ((adapt/spec-colors "green-building" :nearest) "G")) "plain nearest draws the ghost black")
  (is (= (into {"." 0} (map (fn [c] [c 1])) (rest codes)) (adapt/spec-colors "trs80")) "mono: every lit code lit")
  (is (= #{"G" "J" "S" "Z"} (set (keep (fn [[c i]] (when (and (zero? i) (not= c ".")) c)) (adapt/spec-colors "trs80" :nearest))))
      "mono, plain nearest: J, S, Z and G go dark")
  (is (= 6 ((adapt/spec-colors "cga40") "L")) "orange ties brown/yellow; the lower index wins"))

(deftest identity-on-the-spec-grid
  (doseq [d ["green-building" "remote"]]
    (let [{:keys [w h cells loss]} (adapt/adapt pattern {:display d})
          sc (adapt/spec-colors d)]
      (is (= [9 17] [w h]))
      (is (= (vec (for [y (range 17) x (range 9)] (sc (codes (mod (+ x (* 3 y)) 10))))) cells))
      (is (= [0 0 0 0 []] ((juxt :hidden :padding :padding-rows :darkened :merged) loss))))))

(deftest letterbox-placements
  (testing "cga40 40x25: 1x, centred at (15, 4)"
    (let [a (adapt/adapt pattern {:display "cga40"})]
      (is (= ((adapt/spec-colors "cga40") (codes 0)) (at a 15 4)))
      (is (= 153 (count (filter (fn [i] (let [x (rem i 40) y (quot i 40)] (and (<= 15 x 23) (<= 4 y 20))))
                                (range 1000)))))
      (is (= [0 1000 1] ((juxt (comp :hidden :loss) (comp count :cells) (comp :scale :loss)) a)))))
  (testing "hub75 64x32: 1x (17 * 2 > 32), centred at (27, 7)"
    (is (= ((adapt/spec-colors "hub75") (codes 0)) (at (adapt/adapt pattern {:display "hub75"}) 27 7))))
  (testing "trs80 10x12: the top 5 rows are cropped, the stack stays"
    (let [a (adapt/adapt pattern {:display "trs80"})]
      (is (= [45 45 0] ((juxt :hidden :hidden-lit :padding-rows) (:loss (adapt/adapt lit {:display "trs80"})))))
      (is (= 12 (:padding (:loss a))) "one padded column")
      (is (= ((adapt/spec-colors "trs80") (codes (mod (* 3 5) 10))) (at a 0 0)) "device row 0 is source row 5")))
  (testing "ws2812 16x16: one top row cropped"
    (is (= 9 (-> (adapt/adapt lit {:display "ws2812"}) :loss :hidden))))
  (testing "an integer scale when there is room: k = 2 on 20 cells"
    (is (= [nil 0 0 1 1 2 2 3 3 4 4 5 5 6 6 7 7 8 8 nil] (adapt/axis :center 20 9 2)))))

(deftest other-policies
  (testing ":center crops evenly (the extra row from the top); :crop keeps the bottom"
    (is (= (range 3 15) (adapt/axis :center 12 17 1)))
    (is (= (range 5 17) (adapt/axis :end 12 17 1))))
  (testing ":scale on blinkenlights 18x8: every column twice, every other row"
    (let [[xs ys] (adapt/placement :scale 18 8)]
      (is (= (mapcat (fn [x] [x x]) (range 9)) xs))
      (is (= [1 3 5 7 9 11 13 15] ys))
      (is (= [81 0] ((juxt :hidden :padding) (:loss (adapt/adapt lit {:display "blinkenlights" :policy :scale})))))))
  (testing "the default policy on every preset (README table)"
    (is (= {"arcade" [0 367] "blinkenlights" [81 72] "c64" [0 47] "cga40" [0 847] "dc32" [0 27]
            "gameboy" [0 27] "green-building" [0 0] "hub75" [0 1895] "remote" [0 0] "tetris" [0 47]
            "trs80" [45 12] "ws2812" [9 112]}
           (update-vals (adapt/loss-table) (juxt :hidden :padding-cells))))))

(deftest loss-report
  (let [ghosts (frame-of (fn [x _] (if (zero? x) "G" ".")))]
    (is (= 0 (-> (adapt/adapt ghosts {}) :loss :darkened)))
    (is (= 17 (-> (adapt/adapt ghosts {:lit :nearest}) :loss :darkened)))
    (is (= 17 (-> (adapt/adapt ghosts {}) :loss :recolored)) "42,42,42 shows as 85,85,85"))
  (is (= [["G" "I" "J" "L" "O" "S" "T" "W" "Z"]] (-> (adapt/adapt lit {:display "trs80"}) :loss :merged)))
  (is (= 155 (count (adapt/frame->pal16 pattern {} 7)))))

(deftest adapt-properties
  (holds "adapt: w*h cells 0..15 on every preset and policy; black is 0 and, with :keep, lit is never 0"
         (prop/for-all [frame (s/gen ::ds/spec-frame)
                        d (gen/elements caps/display-names)
                        policy (gen/elements (vec adapt/policies))
                        lit (gen/elements [:keep :nearest])]
           (let [{:keys [w h cells loss]} (adapt/adapt frame {:display d :policy policy :lit lit})
                 [xs ys] (adapt/placement policy w h)
                 shown (for [y ys x xs :when (and x y)] (get-in frame [y x]))]
             (and (= (count cells) (* w h) (* (:w (caps/preset d)) (:h (caps/preset d))))
                  (every? #(<= 0 % 15) cells)
                  (= (:hidden loss) (- 153 (count (set (for [y ys x xs :when (and x y)] [y x])))))
                  (or (= lit :nearest)
                      (= (count (filter #(not= [0 0 0] %) shown))
                         (count (filter pos? (for [[i [y x]] (map-indexed vector (for [y ys x xs] [y x]))
                                                   :when (and x y)]
                                               (cells i))))))
                  (zero? (if (= lit :keep) (:darkened loss) 0)))))))
