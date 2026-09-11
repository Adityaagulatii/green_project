(ns tetris.displays.adapt
  "The adapter core: (SPEC 17x9 RGB frame, display profile) -> (a device frame
  in the device's color model, a loss report). Pure; no I/O, no clock.

  A profile's sink is a geometry pipeline plus a color model:

    [:rotate n]                 n quarter turns clockwise
    [:viewport {:w :h :anchor-x :anchor-y :follow}]
                                crop to a w x h window; :follow :top-lit puts
                                the window's top on the topmost lit row (the
                                falling piece or the stack), clamped so the
                                window stays inside, else the anchor decides
    [:scale {:x :y}]            integer replication
    [:pad {:w :h :align-x :align-y}]
                                letterbox into w x h, unlit fill; :center
                                puts the odd cell right/bottom

  Geometry runs on the grid of source coordinates, not on colors, so the
  loss report (which source cells are hidden) and the inverse of a lossless
  profile both fall out of the same index map."
  (:require [clojure.spec.alpha :as s]
            [tetris.displays.specs :as ds]))

(def black [0 0 0])

(defn lit? [rgb] (not= rgb black))
(s/fdef lit? :args (s/cat :rgb ::ds/rgb) :ret boolean?)

;; ------------------------------------------------------------ geometry

(defn- dims [g] [(count (first g)) (count g)])

(defn- coords [w h] (mapv (fn [r] (mapv (fn [c] [r c]) (range w))) (range h)))

(defn- rotate-cw [g]
  (let [[w h] (dims g)]
    (mapv (fn [r] (mapv (fn [c] (get-in g [(- h 1 c) r])) (range h))) (range w))))

(defn- offset [align free]
  (case align
    (:top :left) 0
    (:bottom :right) free
    (quot free 2)))

(defn- viewport [g {:keys [w h follow anchor-x anchor-y]
                    :or {anchor-x :center anchor-y :bottom}} lit-at]
  (let [[gw gh] (dims g)
        w (min (or w gw) gw)
        h (min (or h gh) gh)
        top-lit (when (= follow :top-lit)
                  (first (keep-indexed (fn [i row] (when (some lit-at row) i)) g)))
        top (if top-lit (min top-lit (- gh h)) (offset anchor-y (- gh h)))
        left (offset anchor-x (- gw w))]
    [(mapv #(subvec % left (+ left w)) (subvec g top (+ top h)))
     {:top top :left left :w w :h h}]))

(defn- scale [g {:keys [x y]}]
  (into [] (mapcat (fn [row] (repeat y (into [] (mapcat #(repeat x %)) row)))) g))

(defn- pad [g {:keys [w h align-x align-y] :or {align-x :center align-y :bottom}}]
  (let [[gw gh] (dims g)
        left (offset align-x (- w gw))
        top (offset align-y (- h gh))
        blank (vec (repeat w nil))]
    (-> []
        (into (repeat top blank))
        (into (map (fn [row] (-> (vec (repeat left nil)) (into row)
                                 (into (repeat (- w gw left) nil)))))
              g)
        (into (repeat (- h gh top) blank)))))

(defn index-map
  "Run `pipeline` over the coordinates of `frame`. Returns [grid windows]:
  grid holds the source [row col] shown by each device cell (nil = padding),
  windows the viewport windows used, in order."
  [pipeline frame]
  (let [[w h] (dims frame)
        lit-at (fn [rc] (boolean (and rc (lit? (get-in frame rc)))))]
    (reduce (fn [[g windows] [k o]]
              (case k
                :rotate   [(nth (iterate rotate-cw g) (mod o 4)) windows]
                :viewport (let [[g' win] (viewport g o lit-at)] [g' (conj windows win)])
                :scale    [(scale g o) windows]
                :pad      [(pad g o) windows]))
            [(coords w h) []]
            pipeline)))
(s/fdef index-map
  :args (s/cat :pipeline ::ds/pipeline :frame ::ds/rows)
  :ret (s/tuple (s/coll-of vector? :kind vector?) ::ds/windows))

;; ------------------------------------------------------------ color

(defn- round-div [a b] (quot (+ a (quot b 2)) b))

(def ^:private code-of (into {} (map (fn [[c rgb]] [rgb c])) ds/spec-palette))

(defn- nearest [colors rgb]
  (let [d2 (fn [c] (reduce + (map (fn [a b] (let [d (- a b)] (* d d))) c rgb)))]
    (first (reduce (fn [[bi bd] [i c]] (let [d (d2 c)] (if (< d bd) [i d] [bi bd])))
                   [0 (d2 (first colors))]
                   (map-indexed vector colors)))))

(defn quantize
  "The device value for `rgb` in `color`:
    :rgb      channels rounded to (:bits color)
    :mono     level of max(r,g,b) (the brightest channel, so every SPEC piece
              color is full brightness); a lit cell keeps at least :min-lit
    :palette  the :map entry for a SPEC palette color, else the nearest color
              (squared RGB distance, ties to the lowest index)"
  [color rgb]
  (case (:model color)
    :rgb     (mapv (fn [c bits] (round-div (* c (dec (bit-shift-left 1 bits))) 255))
                   rgb (:bits color))
    :mono    (let [n (:levels color)
                   v (apply max rgb)
                   lvl (round-div (* v (dec n)) 255)]
               (if (pos? v) (max lvl (:min-lit color 0)) lvl))
    :palette (or (some->> (code-of rgb) (get (:map color)))
                 (nearest (:colors color) rgb))))
(s/fdef quantize
  :args (s/cat :color ::ds/color :rgb ::ds/rgb)
  :ret any?
  :fn #(ds/legal-value? (-> % :args :color) (:ret %)))

(defn ->rgb
  "The RGB a device value shows as. Mono is grey; a sink may tint it."
  [color v]
  (case (:model color)
    :rgb     (mapv (fn [x bits] (round-div (* x 255) (dec (bit-shift-left 1 bits))))
                   v (:bits color))
    :mono    (let [g (round-div (* v 255) (dec (:levels color)))] [g g g])
    :palette (nth (:colors color) v)))
(s/fdef ->rgb
  :args (s/with-gen (s/and (s/cat :color ::ds/color :v any?)
                           #(ds/legal-value? (:color %) (:v %)))
          #(clojure.spec.gen.alpha/fmap
            (fn [[c rgb]] [c (quantize c rgb)])
            (clojure.spec.gen.alpha/tuple (s/gen ::ds/color) (s/gen ::ds/rgb))))
  :ret ::ds/rgb)

(defn color-loss
  "What `color` does to the SPEC palette (static): exact? (every color shown
  as itself), distinct? (no two codes share a device value), merged (the
  groups of codes that do)."
  [color]
  (let [shown (mapv (fn [[code rgb]] [code rgb (quantize color rgb)]) ds/spec-palette)
        merged (->> (group-by #(nth % 2) shown)
                    vals
                    (filter #(> (count %) 1))
                    (map #(mapv first %))
                    sort
                    vec)]
    {:exact? (every? (fn [[_ rgb v]] (= rgb (->rgb color v))) shown)
     :distinct? (empty? merged)
     :merged merged}))
(s/fdef color-loss :args (s/cat :color ::ds/color) :ret ::ds/color-loss)

(defn palette-table
  "SPEC code -> the RGB the device shows for it."
  [color]
  (into (sorted-map) (map (fn [[code rgb]] [code (->rgb color (quantize color rgb))]))
        ds/spec-palette))
(s/fdef palette-table :args (s/cat :color ::ds/color)
        :ret (s/map-of (set ds/spec-codes) ::ds/rgb))

;; ------------------------------------------------------------ adapt

(def ^:private blank-frame
  (vec (repeat ds/spec-h (vec (repeat ds/spec-w black)))))

(defn- visible [grid] (into #{} (comp cat (remove nil?)) grid))

(defn geometry-lossless?
  "Does every SPEC cell reach the device, whatever the frame? Only a cropping
  viewport depends on content, and a blank frame already shows its loss."
  [pipeline]
  (= (* ds/spec-w ds/spec-h)
     (count (visible (first (index-map pipeline blank-frame))))))
(s/fdef geometry-lossless? :args (s/cat :pipeline ::ds/pipeline) :ret boolean?)

(defn lossless?
  "A profile is lossless when its geometry keeps every cell and its color
  keeps the SPEC palette codes apart: then `unadapt` recovers the frame."
  [profile]
  (let [{:keys [pipeline color]} (:sink profile)]
    (and (geometry-lossless? pipeline) (:distinct? (color-loss color)))))
(s/fdef lossless? :args (s/cat :profile ::ds/profile) :ret boolean?)

(defn adapt
  "SPEC frame -> {:device device-frame :loss report} for `profile`."
  [profile frame]
  (let [{:keys [pipeline color]} (:sink profile)
        [grid windows] (index-map pipeline frame)
        fill (quantize color black)
        cells (mapv (fn [row] (mapv (fn [rc] (if rc (quantize color (get-in frame rc)) fill)) row))
                    grid)
        [w h] (dims frame)
        seen (visible grid)
        lit-src (for [r (range h) c (range w) :when (lit? (get-in frame [r c]))] [r c])
        [dw dh] (dims cells)]
    {:device {:w dw :h dh :color color :cells cells}
     :loss {:display (:id profile)
            :lossless? (lossless? profile)
            :geometry {:in [w h] :out [dw dh]
                       :hidden (- (* w h) (count seen))
                       :hidden-lit (count (remove seen lit-src))
                       :padding (count (filter nil? (apply concat grid)))
                       :windows windows}
            :color-loss (assoc (color-loss color)
                               :recolored (count (filter (fn [rc]
                                                           (let [rgb (get-in frame rc)]
                                                             (not= rgb (->rgb color (quantize color rgb)))))
                                                         seen)))}}))
(s/fdef adapt
  :args (s/cat :profile ::ds/profile :frame ::ds/spec-frame)
  :ret ::ds/adapted
  :fn (fn [{:keys [args ret]}]
        (let [d (:device ret)]
          (and (= (:w (:profile args)) (:w d))
               (= (:h (:profile args)) (:h d))))))

(defn device-rgb
  "The device frame as RGB rows: what goes on the wire (w*h*3, row-major)."
  [{:keys [color cells]}]
  (mapv (fn [row] (mapv #(->rgb color %) row)) cells))
(s/fdef device-rgb :args (s/cat :device ::ds/device-frame) :ret ::ds/rows)

(defn unadapt
  "Invert `adapt` for a lossless profile: the SPEC frame back from the device
  frame (colors decoded through the SPEC palette). nil for a lossy profile."
  [profile device]
  (when (lossless? profile)
    (let [{:keys [pipeline color]} (:sink profile)
          [grid _] (index-map pipeline blank-frame)
          inv (into {} (map (fn [[_ rgb]] [(quantize color rgb) rgb])) ds/spec-palette)
          pos (reduce (fn [m [rc v]] (if (and rc (not (contains? m rc))) (assoc m rc v) m))
                      {}
                      (map vector (apply concat grid) (apply concat (:cells device))))]
      (mapv (fn [r] (mapv (fn [c] (let [v (pos [r c])] (get inv v (->rgb color v))))
                          (range ds/spec-w)))
            (range ds/spec-h)))))
(s/fdef unadapt
  :args (s/cat :profile ::ds/profile :device ::ds/device-frame)
  :ret (s/nilable ::ds/spec-frame))

(defn rgb-bytes
  "Row-major R,G,B values of `rows` (SPEC §9.4 order), as a vector of ints."
  [rows]
  (into [] (comp cat cat) rows))
(s/fdef rgb-bytes
  :args (s/cat :rows ::ds/rows)
  :ret (s/coll-of ::ds/channel :kind vector?)
  :fn #(= (count (:ret %)) (* 3 (count (-> % :args :rows))
                              (count (first (-> % :args :rows))))))
