(ns tetris.displays.codec
  "Pure frame codecs for wal.sh/tools/display v0.2.1. No I/O, no clock.

  - pal16 and hex decode and encode (spec section 6.4). A pal16 frame is
    w*h octets 0..15, row-major, optionally prefixed by a 2-byte big-endian
    sequence number. A hex frame is h lines of w digits 0-9a-f, each
    LF-terminated, optionally followed by one blank line.
  - The level rule and the 16-entry palette a display announces (section 2).
  - Nearest-palette quantization for rgb24, which is a relay or shim
    concern only (NR-QUANT): squared sRGB distance, ties to the lower index.

  Decoding returns {:cells [idx ...]}, plus :seq when a pal16 frame carries
  the prefix, or {:error reason} with one of the wire's two frame reasons.
  Length is checked before content: a frame of the wrong length is
  \"bad-frame-length\" whatever it holds, and a frame of the right length
  with a cell outside 0..15 is \"bad-format\". A hex frame is checked by
  position once its length is right: every digit position must hold a hex
  digit and every line end an LF, else bad-format. So CRLF lines are
  bad-frame-length when h > 1 and bad-format when h = 1, and a missing
  final LF is bad-frame-length. Upper-case A-F are accepted. These three
  are choices where the text is silent, the same as the contract's Python
  reference (contract/display_contract.py).

  Also: the Blinkenlights interop packets the relay accepts as WebSocket
  binary (capabilities.interop): BLP and MCUF, scaled to 0..15."
  (:require [clojure.spec.alpha :as s]
            [clojure.spec.gen.alpha :as gen]
            [clojure.string :as str]
            [tetris.displays.specs :as ds]))

(defn- grid-cells-gen
  "[w cells] with cells a whole number of rows w wide (lazy, STANDARD 5.1)."
  []
  (gen/bind (gen/elements [1 2 3 9 16 255 256])
            (fn [w] (gen/fmap (fn [rows] [w (vec (apply concat rows))])
                              (gen/vector (gen/vector (gen/choose 0 15) w) 0 4)))))

(def hex-digits "The hex frame's alphabet, index = value." "0123456789abcdef")

;; ------------------------------------------------------------ colours

(defn- parse-hex [s]
  #?(:clj (Long/parseLong s 16) :cljs (js/parseInt s 16)))

(defn hex->rgb
  "\"#AA5500\" -> [170 85 0]."
  [hex]
  (let [n (parse-hex (subs hex 1))]
    [(bit-and (bit-shift-right n 16) 0xff) (bit-and (bit-shift-right n 8) 0xff) (bit-and n 0xff)]))
(s/fdef hex->rgb :args (s/cat :hex ::ds/hex-color) :ret ::ds/rgb)

(defn rgb->hex
  "[170 85 0] -> \"#AA5500\" (upper case, as capabilities.json writes it)."
  [rgb]
  (str/upper-case
   (apply str "#" (mapcat (fn [c] [(nth hex-digits (quot c 16)) (nth hex-digits (rem c 16))]) rgb))))
(s/fdef rgb->hex
  :args (s/cat :rgb ::ds/rgb)
  :ret ::ds/hex-color
  :fn #(= (-> % :args :rgb) (hex->rgb (:ret %))))

(defn level
  "The display-contract level rule: the level shown for index `idx` on a
  palette of `n` entries.

    level(idx) = 0                                when idx = 0
               = max(1, round(idx * (n - 1) / 15)) otherwise

  Index 0 is always unlit and any lit index stays lit. The rounding mode
  never matters: idx*(n-1)/15 = k + 1/2 would need 2*idx*(n-1), which is
  even, to equal 15*(2k+1), which is odd."
  [idx n]
  (if (zero? idx)
    0
    (max 1 (quot (+ (* 2 idx (dec n)) 15) 30))))
(s/fdef level
  :args (s/cat :idx ::ds/idx :n (s/int-in 2 17))
  :ret nat-int?
  :fn (fn [{{:keys [idx n]} :args ret :ret}]
        (and (< ret n) (= (zero? idx) (zero? ret)) (or (not= n 16) (= idx ret)))))

(defn palette16
  "The 16 entries a display shows for indices 0..15: entry i is
  entries[level(i, n)]. Identity on a 16-entry palette; this is the
  `palette` that caps and granted carry."
  [entries]
  (let [n (count entries)]
    (mapv #(nth entries (level % n)) (range 16))))
(s/fdef palette16
  :args (s/cat :entries ::ds/palette)
  :ret ::ds/palette16
  :fn (fn [{{:keys [entries]} :args ret :ret}]
        (and (= (first entries) (first ret)) (= (peek entries) (peek ret))
             (or (not= 16 (count entries)) (= entries ret)))))

(defn- d2 [a b]
  (let [dr (- (nth a 0) (nth b 0)) dg (- (nth a 1) (nth b 1)) db (- (nth a 2) (nth b 2))]
    (+ (* dr dr) (* dg dg) (* db db))))

(defn nearest
  "Index of the entry of `palette` (RGB triples) nearest `rgb`: squared sRGB
  distance, ties to the lower index. With `from`, only indices >= from
  compete."
  ([palette rgb] (nearest palette rgb 0))
  ([palette rgb from]
   (let [n (count palette)]
     (loop [i (inc from) best from bd (d2 (nth palette from) rgb)]
       (if (< i n)
         (let [d (d2 (nth palette i) rgb)]
           (if (< d bd) (recur (inc i) i d) (recur (inc i) best bd)))
         best)))))
(s/fdef nearest
  :args (s/cat :palette ::ds/palette-rgb :rgb ::ds/rgb :from (s/? #{0}))
  :ret nat-int?
  :fn (fn [{{:keys [palette rgb]} :args ret :ret}]
        (let [d (d2 (nth palette ret) rgb)]
          (and (every? #(<= d (d2 % rgb)) palette)
               (every? #(< d (d2 % rgb)) (subvec palette 0 ret))))))

;; ------------------------------------------------------------ frames

(defn octets
  "A binary frame as a vector of ints: a vector as is, a byte array read
  unsigned, any other sequential coll as a vector; nil for anything else."
  [data]
  (cond
    (vector? data) data
    #?(:clj (bytes? data) :cljs (instance? js/Uint8Array data)) (mapv #(bit-and (long %) 0xff) data)
    (sequential? data) (vec data)
    :else nil))
(s/fdef octets :args (s/cat :data any?) :ret (s/nilable vector?))

(defn- idx? [x] (and (int? x) (<= 0 x 15)))
(defn- octet? [x] (and (int? x) (<= 0 x 255)))

(defn decode-pal16
  "Decode a pal16 frame (octets, see `octets`) for a w x h grid."
  [w h data]
  (if-let [bs (octets data)]
    (let [n (* w h) len (count bs)]
      (cond
        (= len n)
        (if (every? idx? bs) {:cells bs} {:error "bad-format"})

        (= len (+ n 2))
        (let [cells (subvec bs 2)]
          (if (and (every? idx? cells) (octet? (bs 0)) (octet? (bs 1)))
            {:cells cells :seq (+ (* 256 (bs 0)) (bs 1))}
            {:error "bad-format"}))

        :else {:error "bad-frame-length"}))
    {:error "bad-format"}))
(s/fdef decode-pal16 :args (s/cat :w ::ds/w :h ::ds/h :data any?) :ret ::ds/decoded)

(def ^:private hexval
  (merge (zipmap hex-digits (range 16)) (zipmap "ABCDEF" (range 10 16))))

(defn- hex-row
  "Append row y's w digit values to transient `out`, or nil if a position is
  not a hex digit or the line end is not LF."
  [text w y out]
  (let [base (* y (inc w))]
    (when (= \newline (nth text (+ base w)))
      (loop [x 0 out out]
        (if (= x w)
          out
          (when-let [v (hexval (nth text (+ base x)))]
            (recur (inc x) (conj! out v))))))))

(defn decode-hex
  "Decode a hex frame (a string) for a w x h grid: by length, then by
  position (see the ns doc)."
  [w h text]
  (if-not (string? text)
    {:error "bad-format"}
    (let [n (* h (inc w))
          len (count text)]
      (cond
        (not (or (= len n) (= len (inc n)))) {:error "bad-frame-length"}
        (and (= len (inc n)) (not= \newline (nth text n))) {:error "bad-format"}
        :else
        (loop [y 0 out (transient [])]
          (if (= y h)
            {:cells (persistent! out)}
            (if-let [out (hex-row text w y out)]
              (recur (inc y) out)
              {:error "bad-format"})))))))
(s/fdef decode-hex :args (s/cat :w ::ds/w :h ::ds/h :text any?) :ret ::ds/decoded)

(defn decode
  "Decode one frame for a w x h grid by its carrier: a string is a hex frame
  (a text message that does not start with `{`), anything else a pal16
  frame (a binary message)."
  [w h data]
  (if (string? data) (decode-hex w h data) (decode-pal16 w h data)))
(s/fdef decode :args (s/cat :w ::ds/w :h ::ds/h :data any?) :ret ::ds/decoded)

(defn decode-rgb24
  "Decode an rgb24 frame (w*h*3 octets, optional 2-byte sequence prefix) and
  quantize each cell to the nearest entry of `palette` (the display's 16
  entries as RGB triples). Relay-only: the sink never quantizes."
  [w h palette data]
  (if-let [bs (octets data)]
    (let [n (* 3 w h) len (count bs)]
      (cond
        (not (or (= len n) (= len (+ n 2)))) {:error "bad-frame-length"}
        (not (every? octet? bs)) {:error "bad-format"}
        :else
        (let [off (- len n)
              q (memoize #(nearest palette %))
              cells (mapv (fn [i] (let [j (+ off (* 3 i))] (q (subvec bs j (+ j 3)))))
                          (range (* w h)))]
          (cond-> {:cells cells}
            (= 2 off) (assoc :seq (+ (* 256 (bs 0)) (bs 1)))))))
    {:error "bad-format"}))
(s/fdef decode-rgb24
  :args (s/cat :w ::ds/w :h ::ds/h :palette ::ds/palette-rgb :data any?)
  :ret ::ds/decoded)

;; ------------------------------------------------------------ interop

(def blp-magic "BLP (capabilities.interop)." 0xDEADBEEF)
(def mcuf-magic "MCUF (capabilities.interop)." 0x23542666)
(def interop-header "Header bytes of both, big-endian." 12)

(defn- u16 [bs at] (+ (* 256 (bs at)) (bs (inc at))))
(defn- u32 [bs at] (+ (* 65536 (u16 bs at)) (u16 bs (+ at 2))))

(defn interop?
  "Does a binary message start with the BLP or MCUF magic? A pal16 frame
  never does: bytes 3 and 4 of both magics are over 15, and they are cells
  even behind a sequence prefix."
  [data]
  (let [bs (octets data)]
    (boolean (and bs (<= 4 (count bs)) (every? octet? (subvec bs 0 4))
                  (contains? #{blp-magic mcuf-magic} (u32 bs 0))))))
(s/fdef interop? :args (s/cat :data any?) :ret boolean?)

(defn scale
  "v in 0..maxval -> 0..top, rounded half up (1 of maxval 2 -> 8 of 15)."
  [v maxval top]
  (quot (+ (* 2 v top) maxval) (* 2 maxval)))
(s/fdef scale :args (s/and (s/cat :v nat-int? :maxval pos-int? :top #{15 255}) #(<= (:v %) (:maxval %)))
        :ret nat-int? :fn #(<= (:ret %) (-> % :args :top)))

(defn parse-interop
  "A BLP or MCUF packet -> {:kind :w :h :channels :maxval :payload}, or
  {:error reason}. Header layouts (big-endian, 12 bytes), as the contract's
  reference reads the Blinkenlights wiki:
    BLP   magic DEADBEEF, frame count u32, width u16, height u16; w*h bytes 0|1
    MCUF  magic 23542666, height u16, width u16, channels u16, maxval u16;
          h*w*channels bytes 0..maxval"
  [data]
  (let [bs (octets data)
        len (count bs)]
    (cond
      (< len 4) {:error "bad-frame-length"}
      (not (interop? bs)) {:error "bad-format"}
      (< len interop-header) {:error "bad-frame-length"}
      :else
      (let [blp? (= blp-magic (u32 bs 0))
            [kind w h ch mv] (if blp?
                               [:blp (u16 bs 8) (u16 bs 10) 1 1]
                               [:mcuf (u16 bs 6) (u16 bs 4) (u16 bs 8) (u16 bs 10)])
            payload (subvec bs interop-header)]
        (cond
          (not (and (<= 1 w 256) (<= 1 h 256))) {:error "bad-format"}
          (not (contains? #{1 3} ch)) {:error "bad-format"}
          (< mv 1) {:error "bad-format"}
          (not= (count payload) (* w h ch)) {:error "bad-frame-length"}
          (not (every? #(and (octet? %) (<= % mv)) payload)) {:error "bad-format"}
          :else {:kind kind :w w :h h :channels ch :maxval mv :payload payload})))))
(s/fdef parse-interop :args (s/cat :data any?) :ret map?)

(defn interop-cells
  "The pal16 cells of a parsed packet: one channel scales to 0..15 (BLP 1 ->
  15); three channels scale to 0..255 and quantize against `palette` (16 RGB
  triples) as rgb24 does."
  [{:keys [w h channels maxval payload]} palette]
  (if (= 1 channels)
    (mapv #(scale % maxval 15) payload)
    (:cells (decode-rgb24 w h palette (mapv #(scale % maxval 255) payload)))))
(s/fdef interop-cells
  :args (s/with-gen (s/cat :packet map? :palette ::ds/palette-rgb)
          #(gen/fmap (fn [[bits pal]] [(parse-interop (into [0xDE 0xAD 0xBE 0xEF 0 0 0 0 0 (count bits) 0 1] bits)) pal])
                     (gen/tuple (gen/vector (gen/choose 0 1) 1 8) (s/gen ::ds/palette-rgb))))
  :ret ::ds/cells)

(defn encode-blp
  "A BLP packet for a w x h grid of bits 0|1."
  [w h bits]
  (into [0xDE 0xAD 0xBE 0xEF 0 0 0 0 (quot w 256) (rem w 256) (quot h 256) (rem h 256)] bits))
(s/fdef encode-blp
  :args (s/cat :w (s/int-in 0 65536) :h (s/int-in 0 65536) :bits (s/coll-of ::ds/octet :gen-max 64))
  :ret ::ds/octets
  :fn #(interop? (:ret %)))

(defn encode-mcuf
  "An MCUF packet: h*w*channels values 0..maxval."
  [w h values channels maxval]
  (into [0x23 0x54 0x26 0x66 (quot h 256) (rem h 256) (quot w 256) (rem w 256)
         0 channels (quot maxval 256) (rem maxval 256)]
        values))
(s/fdef encode-mcuf
  :args (s/cat :w (s/int-in 0 65536) :h (s/int-in 0 65536) :values (s/coll-of ::ds/octet :gen-max 64)
               :channels (s/int-in 0 256) :maxval (s/int-in 0 65536))
  :ret ::ds/octets
  :fn #(interop? (:ret %)))

;; ------------------------------------------------------------ encoders

(defn encode-pal16
  "Cells -> the pal16 octets, with the 2-byte big-endian sequence prefix if
  `seq` is given."
  ([cells] (vec cells))
  ([cells seq] (into [(quot seq 256) (rem seq 256)] cells)))
(s/fdef encode-pal16
  :args (s/cat :cells ::ds/cells :seq (s/? ::ds/seq))
  :ret ::ds/octets
  :fn #(= (count (:ret %)) (+ (count (-> % :args :cells)) (if (-> % :args :seq) 2 0))))

(defn encode-hex
  "Cells of a grid `w` wide -> the hex frame text."
  [w cells]
  (apply str (mapcat (fn [row] (concat (map #(nth hex-digits %) row) ["\n"]))
                     (partition w cells))))
(s/fdef encode-hex
  :args (s/with-gen (s/and (s/cat :w ::ds/w :cells ::ds/cells)
                           #(zero? (rem (count (:cells %)) (:w %))))
          grid-cells-gen)
  :ret string?
  :fn #(= (count (:ret %)) (* (inc (-> % :args :w)) (quot (count (-> % :args :cells)) (-> % :args :w)))))

(defn encode
  "Cells of a grid `w` wide in `format` (\"pal16\" or \"hex\")."
  [format w cells]
  (case format
    "pal16" (encode-pal16 cells)
    "hex" (encode-hex w cells)))
(s/fdef encode
  :args (s/with-gen (s/and (s/cat :format ::ds/format :w ::ds/w :cells ::ds/cells)
                           #(zero? (rem (count (:cells %)) (:w %))))
          #(gen/fmap (fn [[f [w cells]]] [f w cells])
                     (gen/tuple (gen/elements ["pal16" "hex"]) (grid-cells-gen))))
  :ret (s/or :pal16 ::ds/octets :hex string?))
