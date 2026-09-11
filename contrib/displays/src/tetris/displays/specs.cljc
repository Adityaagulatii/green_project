(ns tetris.displays.specs
  "clojure.spec for the shared data of wal.sh/tools/display v0.2.1
  (https://clojure.org/guides/spec): palette indices, frames, palettes,
  presets, the SPEC 17x9 RGB frame. Each machine's own state, event and
  effect specs live next to its fold (tetris.displays.core for the sink,
  tetris.displays.lease for the relay).

  Generators are built lazily, inside fns (STANDARD section 5.1), so this
  namespace loads without test.check on the classpath."
  (:require [clojure.spec.alpha :as s]
            [clojure.spec.gen.alpha :as gen]))

;; ------------------------------------------------------------ wire atoms

(s/def ::idx (s/int-in 0 16))
(s/def ::octet (s/int-in 0 256))
(s/def ::octets (s/coll-of ::octet :kind vector?))
(s/def ::dim (s/int-in 1 257))
(s/def ::w ::dim)
(s/def ::h ::dim)
(s/def ::fps (s/int-in 1 61))
(s/def ::cells (s/coll-of ::idx :kind vector?))
(s/def ::seq (s/int-in 0 65536))

(s/def ::format #{"pal16" "hex"})
(s/def ::source-format #{"pal16" "hex" "rgb24"})

(def reasons
  "The v0.2.1 error reasons, and the only ones the relay sends."
  #{"not-holder" "bad-frame-length" "rate" "bad-format" "unknown-op"})

(s/def ::reason reasons)
(s/def ::frame-reason #{"bad-frame-length" "bad-format"})
(s/def ::error ::frame-reason)

(s/def ::decoded
  (s/or :ok (s/keys :req-un [::cells] :opt-un [::seq])
        :err (s/keys :req-un [::error])))

;; ------------------------------------------------------------ colours

(s/def ::channel (s/int-in 0 256))
(s/def ::rgb (s/tuple ::channel ::channel ::channel))

(def ^:private hexchars "0123456789ABCDEF")

(defn- hex-gen []
  (gen/fmap (fn [cs] (apply str "#" (map #(nth hexchars %) cs)))
            (gen/vector (gen/choose 0 15) 6)))

(s/def ::hex-color
  (s/with-gen (s/and string? #(re-matches #"#[0-9A-Fa-f]{6}" %)) hex-gen))

(s/def ::palette (s/coll-of ::hex-color :kind vector? :min-count 2 :max-count 16))
(s/def ::palette16 (s/coll-of ::hex-color :kind vector? :count 16))
(s/def ::palette-rgb (s/coll-of ::rgb :kind vector? :min-count 1 :max-count 16))

;; ------------------------------------------------------------ presets

(s/def ::aspect (s/with-gen (s/and number? #(<= 0.25 % 4)) #(gen/elements [0.25 1 1.2 1.5 4])))
(s/def ::gap (s/with-gen (s/and number? #(<= 0 % 1)) #(gen/elements [0 0.12 0.35 1])))
(s/def ::kind #{"text-mode" "field" "facade" "badge" "panel"})
(s/def ::levels #{2 4 8 16})
(s/def ::mono boolean?)
(s/def ::note string?)
(s/def :tetris.displays.preset/palette #{"cga" "c64" "gb" "mono" "grey8" "pico8"})
(s/def :tetris.displays.preset/format #{"pal16"})

(s/def ::preset
  (s/keys :req-un [::w ::h ::aspect ::gap :tetris.displays.preset/palette ::fps
                   ::kind ::levels ::mono :tetris.displays.preset/format]
          :opt-un [::note]))

(s/def ::spec string?)
(s/def ::default string?)
(s/def ::formats (s/coll-of ::format :kind vector?))
(s/def ::palettes (s/map-of keyword? ::palette))
(s/def ::displays (s/map-of keyword? ::preset))
(s/def ::caps-edn (s/keys :req-un [::spec ::default ::formats ::palettes ::displays]))

;; ------------------------------------------------------------ the SPEC frame

(def spec-w "SPEC section 2.1: columns." 9)
(def spec-h "SPEC section 2.1: rows." 17)

(def spec-palette
  "SPEC section 4.1, in table order: [code rgb]."
  [["." [0 0 0]] ["W" [255 255 255]] ["I" [0 255 255]] ["J" [0 0 255]]
   ["L" [255 170 0]] ["O" [255 255 0]] ["S" [0 255 0]] ["Z" [255 0 0]]
   ["T" [153 0 255]] ["G" [42 42 42]]])

(defn- spec-cell-gen []
  (gen/frequency [[8 (gen/elements (mapv second spec-palette))] [1 (s/gen ::rgb)]]))

(s/def ::spec-frame
  (s/with-gen
    (s/coll-of (s/coll-of ::rgb :kind vector? :count spec-w) :kind vector? :count spec-h)
    #(gen/vector (gen/vector (spec-cell-gen) spec-w) spec-h)))
