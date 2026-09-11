(ns tetris.displays.adapt
  "SPEC 17x9 RGB frames (the Tetris engine's palette, SPEC section 4.1) ->
  pal16 indices on any wal.sh/tools/display v0.2.1 preset, so the game can
  feed any display. Pure; the result goes on the wire with
  codec/encode-pal16.

  Colour. Each SPEC colour goes to the nearest entry of the display's
  16-entry palette (codec/palette16, which already applies the level rule):
  squared sRGB distance, ties to the lower index. With :lit :keep (the
  default) black goes to 0 and every other colour to the nearest LIT entry
  (1..15), so a lit SPEC cell never goes dark; this is the display
  contract's own rule (index 0 is unlit, any lit index stays lit). The plain
  rule, :lit :nearest, draws ghost G (42,42,42) as black on cga, and J, S
  and Z as black on the mono presets.

  Geometry, the loss policy, when the grid is not 9 x 17:
    :letterbox  (default) the largest integer scale k with 9k <= w and
                17k <= h, at least 1, centred with unlit bars; an axis the
                1x field still overflows is cropped from the TOP, so the
                stack and the floor stay and the spawn rows go.
    :center     1:1, centred on both axes: pads or crops evenly; an odd
                leftover pads right/bottom and crops the extra top row.
    :crop       1:1, columns centred, rows anchored at the bottom.
    :scale      nearest-neighbour resample onto the whole grid (cell-centre
                sampling): no padding; rows or columns repeat or drop, and
                the aspect changes.

  `loss-table` computes what the default does on each preset (source
  cells hidden / device cells unlit, for a fully lit frame):
    green-building, remote 0/0   tetris, c64 0/47   dc32, gameboy 0/27
    cga40 0/847   hub75 0/1895   arcade 0/367
    trs80 45/12 (top 5 rows)   ws2812 9/112 (top row)
    blinkenlights 81/72 (top 9 rows; :scale also hides 81, every other row)
  adapt-test holds these numbers."
  (:require [clojure.spec.alpha :as s]
            [tetris.displays.caps :as caps]
            [tetris.displays.codec :as codec]
            [tetris.displays.specs :as ds]))

(def spec-w ds/spec-w)
(def spec-h ds/spec-h)
(def policies #{:letterbox :center :crop :scale})
(def default-policy :letterbox)
(def black [0 0 0])

(s/def ::display (set caps/display-names))
(s/def ::policy policies)
(s/def ::lit #{:keep :nearest})
(s/def ::opts (s/keys :opt-un [::display ::policy ::lit]))

;; ------------------------------------------------------------ colour

(defn display-rgb
  "The 16 colours display `d` shows for indices 0..15 (level rule applied)."
  [d]
  (mapv codec/hex->rgb (codec/palette16 (caps/palette (:palette (caps/preset d))))))
(s/fdef display-rgb :args (s/cat :d ::display) :ret (s/coll-of ::ds/rgb :count 16))

(defn color-index
  "The pal16 index for `rgb` on a display showing `pal` (16 RGB triples)."
  [pal rgb lit]
  (cond
    (= lit :nearest) (codec/nearest pal rgb)
    (= rgb black) 0
    :else (codec/nearest pal rgb 1)))
(s/fdef color-index
  :args (s/cat :pal (s/coll-of ::ds/rgb :kind vector? :count 16) :rgb ::ds/rgb :lit ::lit)
  :ret ::ds/idx
  :fn (fn [{{:keys [rgb lit]} :args ret :ret}]
        (or (= lit :nearest) (= (= rgb black) (zero? ret)))))

(defn spec-colors
  "SPEC code -> pal16 index on display `d`."
  ([d] (spec-colors d :keep))
  ([d lit]
   (let [pal (display-rgb d)]
     (into (sorted-map) (map (fn [[code rgb]] [code (color-index pal rgb lit)])) ds/spec-palette))))
(s/fdef spec-colors :args (s/cat :d ::display :lit (s/? ::lit))
        :ret (s/map-of (set (map first ds/spec-palette)) ::ds/idx))

;; ------------------------------------------------------------ geometry

(defn- floor-half [a] (if (neg? a) (- (quot (inc (- a)) 2)) (quot a 2)))

(defn axis
  "For each of `d` device cells along one axis, the source coordinate it
  shows (nil = padding), for `n` source cells at integer scale `k`. Modes:
  :center, :end (anchored at the far edge: the bottom), :scale (resample; k
  ignored)."
  [mode d n k]
  (if (= mode :scale)
    (mapv #(quot (* (inc (* 2 %)) n) (* 2 d)) (range d))
    (let [shown (* n k)
          off (if (= mode :end) (- d shown) (floor-half (- d shown)))]
      (mapv (fn [x] (let [u (- x off)] (when (< -1 u shown) (quot u k)))) (range d)))))
(s/fdef axis
  :args (s/cat :mode #{:center :end :scale} :d (s/int-in 1 257) :n (s/int-in 1 33) :k (s/int-in 1 29))
  :ret (s/coll-of (s/nilable nat-int?) :kind vector?)
  :fn (fn [{{:keys [d n]} :args ret :ret}]
        (and (= d (count ret)) (every? #(or (nil? %) (< % n)) ret))))

(defn placement
  "[xs ys k] for `policy` on a w x h grid: the source column of each device
  column, the source row of each device row (nil = padding), and the
  integer scale (nil for :scale)."
  [policy w h]
  (case policy
    :scale [(axis :scale w spec-w 1) (axis :scale h spec-h 1) nil]
    :center [(axis :center w spec-w 1) (axis :center h spec-h 1) 1]
    :crop [(axis :center w spec-w 1) (axis :end h spec-h 1) 1]
    :letterbox (let [k (max 1 (min (quot w spec-w) (quot h spec-h)))]
                 [(axis :center w spec-w k) (axis (if (<= (* spec-h k) h) :center :end) h spec-h k) k])))
(s/fdef placement :args (s/cat :policy ::policy :w ::ds/w :h ::ds/h) :ret vector?)

;; ------------------------------------------------------------ adapt

(defn adapt
  "A SPEC frame (17 rows of 9 [r g b]) -> {:display :w :h :cells :loss} on
  preset :display (default green-building), with :policy (default
  :letterbox) and :lit (default :keep). The loss report counts source
  cells not shown (:hidden, :hidden-lit), device cells showing nothing
  (:padding), shown cells whose device colour differs from the SPEC colour
  (:recolored), lit cells drawn as index 0 (:darkened), and the SPEC codes
  that share an index (:merged)."
  ([frame] (adapt frame {}))
  ([frame {:keys [display policy lit] :or {display caps/default-display policy default-policy lit :keep}}]
   (let [{:keys [w h]} (caps/preset display)
         pal (display-rgb display)
         [xs ys k] (placement policy w h)
         q (memoize #(color-index pal % lit))
         cells (vec (for [y ys x xs] (if (and x y) (q (get-in frame [y x])) 0)))
         shown (set (for [y ys :when y x xs :when x] [y x]))
         lit? (fn [yx] (not= black (get-in frame yx)))
         src (for [y (range spec-h) x (range spec-w)] [y x])]
     {:display (name display) :w w :h h :cells cells
      :loss {:policy policy :lit lit :scale k :in [spec-w spec-h] :out [w h]
             :hidden (- (* spec-w spec-h) (count shown))
             :hidden-lit (count (remove shown (filter lit? src)))
             :padding (* (count (filter nil? xs)) h)
             :padding-rows (count (filter nil? ys))
             :recolored (count (filter (fn [yx] (let [rgb (get-in frame yx)] (not= rgb (nth pal (q rgb))))) shown))
             :darkened (count (filter (fn [yx] (and (lit? yx) (zero? (q (get-in frame yx))))) shown))
             :merged (->> ds/spec-palette
                          (group-by #(color-index pal (second %) lit))
                          vals
                          (map #(vec (sort (map first %))))
                          (filter #(> (count %) 1))
                          sort
                          vec)}})))
(s/fdef adapt
  :args (s/cat :frame ::ds/spec-frame :opts (s/? ::opts))
  :ret (s/keys :req-un [::ds/w ::ds/h ::ds/cells])
  :fn (fn [{:keys [ret]}]
        (and (= (count (:cells ret)) (* (:w ret) (:h ret)))
             (<= 0 (-> ret :loss :hidden-lit) (-> ret :loss :hidden) (* spec-w spec-h)))))

(defn frame->pal16
  "A SPEC frame as pal16 octets for display `d` (optionally with a 2-byte
  sequence prefix), ready for the relay."
  ([frame opts] (codec/encode-pal16 (:cells (adapt frame opts))))
  ([frame opts seq] (codec/encode-pal16 (:cells (adapt frame opts)) seq)))
(s/fdef frame->pal16 :args (s/cat :frame ::ds/spec-frame :opts ::opts :seq (s/? ::ds/seq)) :ret ::ds/octets)

(defn loss-table
  "What `policy` does on every preset, for a frame with every cell lit:
  d -> {:w :h :scale :hidden :padding-cells}."
  ([] (loss-table default-policy))
  ([policy]
   (let [lit-frame (vec (repeat spec-h (vec (repeat spec-w [255 255 255]))))]
     (into (sorted-map)
           (map (fn [d]
                  (let [{:keys [w h loss cells]} (adapt lit-frame {:display d :policy policy})]
                    [d {:w w :h h :scale (:scale loss) :hidden (:hidden loss)
                        :padding-cells (count (filter zero? cells))}])))
           caps/display-names))))
(s/fdef loss-table :args (s/? (s/cat :policy ::policy)) :ret map?)
