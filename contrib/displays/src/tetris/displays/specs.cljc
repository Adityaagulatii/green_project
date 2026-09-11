(ns tetris.displays.specs
  "clojure.spec for SPEC frames, display profiles, device frames and loss
  reports (https://clojure.org/guides/spec). The relay's message and event
  specs live next to the fold, in tetris.displays.lease.

  Generators are built lazily, inside fns (STANDARD section 5.1), so this
  namespace loads without test.check on the classpath."
  (:require [clojure.spec.alpha :as s]
            [clojure.spec.gen.alpha :as gen]))

;; ------------------------------------------------------------ SPEC facts

(def spec-w "SPEC §2.1: columns." 9)
(def spec-h "SPEC §2.1: rows." 17)
(def spec-fps "SPEC §2.3: at most 30 sends per second." 30)

(def spec-palette
  "SPEC §4.1, in table order: [code rgb]."
  [["." [0 0 0]] ["W" [255 255 255]] ["I" [0 255 255]] ["J" [0 0 255]]
   ["L" [255 170 0]] ["O" [255 255 0]] ["S" [0 255 0]] ["Z" [255 0 0]]
   ["T" [153 0 255]] ["G" [42 42 42]]])

(def spec-codes (mapv first spec-palette))

(defn palette-rgb? [rgb] (boolean (some #(= rgb (second %)) spec-palette)))

;; ------------------------------------------------------------ colors, frames

(s/def ::channel (s/int-in 0 256))
(s/def ::rgb (s/tuple ::channel ::channel ::channel))

(defn- palette-rgb-gen [] (gen/elements (mapv second spec-palette)))
(defn- cell-gen [] (gen/frequency [[6 (palette-rgb-gen)] [1 (s/gen ::rgb)]]))

(defn- rectangular? [rows]
  (and (seq rows) (seq (first rows)) (apply = (map count rows))))

(s/def ::rows
  (s/with-gen
    (s/and (s/coll-of (s/coll-of ::rgb :kind vector? :min-count 1)
                      :kind vector? :min-count 1)
           rectangular?)
    #(gen/bind (gen/tuple (gen/choose 1 12) (gen/choose 1 12))
               (fn [[w h]] (gen/vector (gen/vector (cell-gen) w) h)))))

(s/def ::spec-frame
  (s/with-gen
    (s/coll-of (s/coll-of ::rgb :kind vector? :count spec-w)
               :kind vector? :count spec-h)
    #(gen/vector (gen/vector (cell-gen) spec-w) spec-h)))

(s/def ::palette-frame
  (s/with-gen
    (s/and ::spec-frame #(every? palette-rgb? (apply concat %)))
    #(gen/vector (gen/vector (palette-rgb-gen) spec-w) spec-h)))

;; ------------------------------------------------------------ geometry ops

(s/def ::w (s/int-in 1 257))
(s/def ::h (s/int-in 1 257))
(s/def ::x (s/int-in 1 9))
(s/def ::y (s/int-in 1 9))
(s/def ::align-x #{:left :center :right})
(s/def ::align-y #{:top :center :bottom})
(s/def ::anchor-x ::align-x)
(s/def ::anchor-y ::align-y)
(s/def ::follow #{:top-lit})
(s/def ::quarter-turns #{0 1 2 3})

(s/def ::viewport-opts (s/keys :opt-un [::w ::h ::follow ::anchor-x ::anchor-y]))
(s/def ::scale-opts (s/keys :req-un [::x ::y]))
(s/def ::pad-opts (s/keys :req-un [::w ::h] :opt-un [::align-x ::align-y]))

(s/def ::op
  (s/or :rotate   (s/tuple #{:rotate} ::quarter-turns)
        :viewport (s/tuple #{:viewport} ::viewport-opts)
        :scale    (s/tuple #{:scale} ::scale-opts)
        :pad      (s/tuple #{:pad} ::pad-opts)))

(s/def ::pipeline (s/coll-of ::op :kind vector? :max-count 8))

(defn pipeline-dims
  "Device [w h] after `pipeline` on a [w h] input, or nil when a :pad target
  is smaller than its input. Static: a :follow viewport never changes dims."
  [pipeline [w h]]
  (reduce (fn [[w h] [k o]]
            (case k
              :rotate   (if (odd? o) [h w] [w h])
              :viewport [(min (or (:w o) w) w) (min (or (:h o) h) h)]
              :scale    [(* w (:x o)) (* h (:y o))]
              :pad      (if (and (<= w (:w o)) (<= h (:h o)))
                          [(:w o) (:h o)]
                          (reduced nil))))
          [w h] pipeline))

;; ------------------------------------------------------------ color models

(s/def ::model #{:rgb :mono :palette})
(s/def ::bits (s/tuple (s/int-in 1 9) (s/int-in 1 9) (s/int-in 1 9)))
(s/def ::levels (s/int-in 2 257))
(s/def ::min-lit (s/int-in 0 256))
(s/def ::colors (s/coll-of ::rgb :kind vector? :min-count 1 :max-count 256))
(s/def ::map (s/map-of (set spec-codes) nat-int?))
(s/def ::ink ::rgb)

(defmulti color-model :model)
(defmethod color-model :rgb [_] (s/keys :req-un [::model ::bits]))
(defmethod color-model :mono [_]
  (s/and (s/keys :req-un [::model ::levels] :opt-un [::min-lit ::ink])
         #(< (:min-lit % 0) (:levels %))))
(defmethod color-model :palette [_]
  (s/and (s/keys :req-un [::model ::colors] :opt-un [::map])
         #(every? (fn [i] (< i (count (:colors %)))) (vals (:map %)))))

(defn- color-gen []
  (gen/one-of
   [(gen/fmap (fn [bits] {:model :rgb :bits bits})
              (gen/elements [[8 8 8] [5 6 5] [3 3 2] [1 1 1]]))
    (gen/fmap (fn [[n m]] {:model :mono :levels n :min-lit (min m (dec n))})
              (gen/tuple (gen/elements [2 3 4 16 256]) (gen/choose 0 1)))
    (gen/fmap (fn [colors] {:model :palette :colors colors})
              (gen/vector (s/gen ::rgb) 1 16))
    (gen/return {:model :palette
                 :colors (mapv second spec-palette)
                 :map (into {} (map-indexed (fn [i [c _]] [c i])) spec-palette)})]))

(s/def ::color (s/with-gen (s/multi-spec color-model :model) color-gen))

(defn legal-value?
  "Is `v` a value the color model can hold?"
  [color v]
  (case (:model color)
    :rgb     (and (vector? v) (= 3 (count v))
                  (every? true? (map (fn [x b] (and (int? x) (<= 0 x) (< x (bit-shift-left 1 b))))
                                     v (:bits color))))
    :mono    (and (int? v) (<= 0 v) (< v (:levels color)))
    :palette (and (int? v) (<= 0 v) (< v (count (:colors color))))
    false))

;; ------------------------------------------------------------ profiles

(s/def ::id (s/with-gen (s/and string? #(re-matches #"[a-z0-9][a-z0-9-]*" %))
              #(gen/elements ["green-building" "trs80" "gen-a" "gen-b"])))
(s/def ::fps (s/int-in 1 61))
(s/def ::aspect (s/with-gen (s/and number? pos?) #(gen/elements [1 1.5 2])))
(s/def ::gap (s/with-gen (s/and number? #(<= 0 % 0.5)) #(gen/elements [0 0.12 0.35])))
(s/def ::look #{"block" "led" "crt" "dmg"})
(s/def ::policy string?)
(s/def ::sink (s/keys :req-un [::pipeline ::color] :opt-un [::policy]))

(defn- dims-match? [p]
  (= [(:w p) (:h p)] (pipeline-dims (get-in p [:sink :pipeline]) [spec-w spec-h])))

(defn- profile-gen
  "Valid profiles by construction: rotate?, viewport?, scale?, pad?."
  []
  (gen/fmap
   (fn [[turns crop? [vw vh] follow? [sx sy] [ex ey] [ax ay] color fps]]
     (let [p0 (cond-> [] (pos? turns) (conj [:rotate turns]))
           [w0 h0] (pipeline-dims p0 [spec-w spec-h])
           p1 (cond-> p0 crop?
                (conj [:viewport (cond-> {:w (min vw w0) :h (min vh h0)
                                          :anchor-x ax :anchor-y ay}
                                   follow? (assoc :follow :top-lit))]))
           p2 (cond-> p1 (or (> sx 1) (> sy 1)) (conj [:scale {:x sx :y sy}]))
           [w2 h2] (pipeline-dims p2 [spec-w spec-h])
           p3 (cond-> p2 (or (pos? ex) (pos? ey))
                (conj [:pad {:w (+ w2 ex) :h (+ h2 ey) :align-x ax :align-y ay}]))
           [w h] (pipeline-dims p3 [spec-w spec-h])]
       {:id "gen" :w w :h h :fps fps :sink {:pipeline p3 :color color}}))
   (gen/tuple (gen/elements [0 0 1 2 3])
              (gen/boolean)
              (gen/tuple (gen/choose 1 17) (gen/choose 1 17))
              (gen/boolean)
              (gen/tuple (gen/choose 1 3) (gen/choose 1 3))
              (gen/tuple (gen/choose 0 4) (gen/choose 0 4))
              (gen/tuple (gen/elements [:left :center :right])
                         (gen/elements [:top :center :bottom]))
              (color-gen)
              (gen/choose 1 30))))

(s/def ::profile
  (s/with-gen
    ;; nonconforming: dims-match? reads the raw ops, not s/or-tagged ones
    (s/and (s/nonconforming (s/keys :req-un [::id ::w ::h ::fps ::sink]
                                    :opt-un [::aspect ::gap ::look]))
           dims-match?)
    profile-gen))

;; ------------------------------------------------------------ device frames

(s/def ::cells (s/coll-of vector? :kind vector? :min-count 1))

(s/def ::device-frame
  (s/and (s/nonconforming (s/keys :req-un [::w ::h ::color ::cells]))
         #(= (:h %) (count (:cells %)))
         #(every? (fn [row] (= (:w %) (count row))) (:cells %))
         #(every? (fn [v] (legal-value? (:color %) v)) (apply concat (:cells %)))))

;; ------------------------------------------------------------ loss reports

(s/def ::display ::id)
(s/def ::lossless? boolean?)
(s/def ::in (s/tuple ::w ::h))
(s/def ::out (s/tuple ::w ::h))
(s/def ::hidden nat-int?)
(s/def ::hidden-lit nat-int?)
(s/def ::padding nat-int?)
(s/def ::top nat-int?)
(s/def ::left nat-int?)
(s/def ::window (s/keys :req-un [::top ::left ::w ::h]))
(s/def ::windows (s/coll-of ::window :kind vector?))
(s/def ::geometry (s/keys :req-un [::in ::out ::hidden ::hidden-lit ::padding ::windows]))
(s/def ::exact? boolean?)
(s/def ::distinct? boolean?)
(s/def ::merged (s/coll-of (s/coll-of (set spec-codes) :kind vector?) :kind vector?))
(s/def ::recolored nat-int?)
(s/def ::color-loss (s/keys :req-un [::exact? ::distinct? ::merged] :opt-un [::recolored]))
(s/def ::loss (s/keys :req-un [::display ::lossless? ::geometry ::color-loss]))
(s/def ::device ::device-frame)
(s/def ::adapted (s/keys :req-un [::device ::loss]))
