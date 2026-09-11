(ns tetris.displays.codec-test
  "Frame codecs, the level rule and the quantizer, at their boundaries."
  (:require [clojure.test :refer [deftest is testing use-fixtures]]
            [clojure.test.check.generators :as gen]
            [clojure.test.check.properties :as prop]
            [tetris.displays.caps :as caps]
            [tetris.displays.codec :as codec]
            [tetris.displays.prop :refer [holds instrument-fixture]]))

(use-fixtures :once instrument-fixture)

(def cga (mapv codec/hex->rgb (caps/palette "cga")))

(defn- d2 [a b] (reduce + (map (fn [x y] (* (- x y) (- x y))) a b)))

;; ------------------------------------------------------------ level rule

(deftest level-rule-boundaries
  (testing "idx 0, 1 and 15 for palette sizes 2, 4, 8 and 16"
    (is (= {2 [0 1 1] 4 [0 1 3] 8 [0 1 7] 16 [0 1 15]}
           (into {} (for [n [2 4 8 16]] [n (mapv #(codec/level % n) [0 1 15])])))))
  (testing "whole tables"
    (is (= [0 1 1 1 1 1 1 1 1 1 1 1 1 1 1 1] (mapv #(codec/level % 2) (range 16))))
    (is (= [0 1 1 1 1 1 1 1 2 2 2 2 2 3 3 3] (mapv #(codec/level % 4) (range 16))))
    (is (= [0 1 1 1 2 2 3 3 4 4 5 5 6 6 7 7] (mapv #(codec/level % 8) (range 16))))
    (is (= (vec (range 16)) (mapv #(codec/level % 16) (range 16)))))
  (testing "palette16, the palette caps and granted announce"
    (is (= (caps/palette "cga") (codec/palette16 (caps/palette "cga"))))
    (is (= (into ["#0F380F"] (concat (repeat 7 "#306230") (repeat 5 "#8BAC0F") (repeat 3 "#9BBC0F")))
           (codec/palette16 (caps/palette "gb"))))
    (is (= (into ["#000000"] (repeat 15 "#FFFFFF")) (codec/palette16 (caps/palette "mono"))))))

(deftest level-rule-properties
  (holds "codec/level: 0 iff 0, below n, monotone, identity at 16"
         (prop/for-all [n (gen/choose 2 16)]
           (let [ls (mapv #(codec/level % n) (range 16))]
             (and (zero? (ls 0)) (every? pos? (rest ls)) (every? #(< % n) ls)
                  (apply <= ls) (= (peek ls) (dec n))
                  (or (not= n 16) (= ls (vec (range 16)))))))))

;; ------------------------------------------------------------ quantizer

(deftest quantizer-ties
  (testing "exact ties go to the lower index"
    (is (= 0 (codec/nearest [[0 0 0] [2 0 0]] [1 0 0])))
    (is (= 1 (codec/nearest [[9 9 9] [0 0 0] [0 0 0]] [0 0 0])))
    (is (= 6 (codec/nearest cga [255 170 0]))
        "SPEC orange: brown #AA5500 and yellow #FFFF55 are both 14450 away")
    (is (= (d2 [255 170 0] (cga 6)) (d2 [255 170 0] (cga 14)) 14450)))
  (testing "nearest, and nearest lit"
    (is (= 14 (codec/nearest cga [255 255 0])))
    (is (= 0 (codec/nearest cga [42 42 42])) "SPEC ghost grey: black 5292 beats dark grey 5547")
    (is (= 8 (codec/nearest cga [42 42 42] 1))))
  (testing "rgb24 (relay-only): quantized, prefix, lengths, octets"
    (is (= {:cells [6 15 0]} (codec/decode-rgb24 3 1 cga [255 170 0 255 255 255 0 0 0])))
    (is (= {:cells [15] :seq 65535} (codec/decode-rgb24 1 1 cga [255 255 255 255 255])))
    (is (= {:error "bad-frame-length"} (codec/decode-rgb24 1 1 cga [1 2])))
    (is (= {:error "bad-frame-length"} (codec/decode-rgb24 1 1 cga [1 2 3 4])))
    (is (= {:error "bad-format"} (codec/decode-rgb24 1 1 cga [1 2 256])))))

(deftest quantizer-properties
  (holds "codec/nearest: minimal distance, ties to the lower index"
         (prop/for-all [pal (gen/vector (gen/vector (gen/choose 0 255) 3) 1 16)
                        rgb (gen/vector (gen/choose 0 255) 3)]
           (let [i (codec/nearest pal rgb)
                 d (d2 (pal i) rgb)]
             (and (every? #(<= d (d2 % rgb)) pal)
                  (every? #(< d (d2 % rgb)) (subvec pal 0 i)))))))

;; ------------------------------------------------------------ pal16

(deftest pal16-boundaries
  (let [n 153 cells (vec (repeat n 15))]
    (testing "lengths w*h and w*h+2, and +-1 of each (green-building 9x17)"
      (is (= {:cells cells} (codec/decode-pal16 9 17 cells)))
      (is (= {:cells cells :seq 258} (codec/decode-pal16 9 17 (into [1 2] cells))))
      (doseq [len [(dec n) (inc n) (+ n 3)]]
        (is (= {:error "bad-frame-length"} (codec/decode-pal16 9 17 (vec (repeat len 0)))) (str len))))
    (testing "byte values 15, 16 and 255; the prefix may hold any byte"
      (is (= {:cells [15]} (codec/decode-pal16 1 1 [15])))
      (is (= {:error "bad-format"} (codec/decode-pal16 1 1 [16])))
      (is (= {:error "bad-format"} (codec/decode-pal16 1 1 [255])))
      (is (= {:cells [0] :seq 65535} (codec/decode-pal16 1 1 [255 255 0])))
      (is (= {:cells [0] :seq 0} (codec/decode-pal16 1 1 [0 0 0])))
      (is (= {:error "bad-format"} (codec/decode-pal16 1 1 (byte-array [-1]))) "a JVM byte -1 is 255"))
    (testing "length before content"
      (is (= {:error "bad-frame-length"} (codec/decode-pal16 1 1 [16 16]))))
    (testing "the largest grid: 256x256 = 65,536 cells, 65,538 bytes with the prefix"
      (let [big (vec (repeat 65536 7))]
        (is (= {:cells big} (codec/decode-pal16 256 256 big)))
        (is (= 65538 (count (codec/encode-pal16 big 1))))
        (is (= {:cells big :seq 1} (codec/decode-pal16 256 256 (codec/encode-pal16 big 1))))
        (doseq [len [65535 65537 65539]]
          (is (= {:error "bad-frame-length"} (codec/decode-pal16 256 256 (vec (repeat len 0)))) (str len)))))
    (testing "1x1"
      (is (= {:cells [7]} (codec/decode 1 1 [7])))
      (is (= {:error "bad-frame-length"} (codec/decode 1 1 []))))))

;; ------------------------------------------------------------ hex

(deftest hex-boundaries
  (let [cells [0 1 15 10 11 12]
        text "01f\nabc\n"]
    (is (= text (codec/encode-hex 3 cells)))
    (is (= {:cells cells} (codec/decode-hex 3 2 text)))
    (is (= {:cells cells} (codec/decode-hex 3 2 (str text "\n"))) "the closing blank line")
    (testing "digits f, g and upper-case F"
      (is (= {:cells cells} (codec/decode-hex 3 2 "01F\nABC\n")) "A-F accepted, as the reference does")
      (is (= {:error "bad-format"} (codec/decode-hex 3 2 "01g\nabc\n")))
      (is (= {:error "bad-format"} (codec/decode-hex 3 2 "01G\nabc\n"))))
    (testing "line endings"
      (is (= {:error "bad-frame-length"} (codec/decode-hex 3 2 "01f\nabc")) "a missing final LF")
      (is (= {:error "bad-frame-length"} (codec/decode-hex 3 2 "01f\r\nabc\r\n")) "CRLF, h > 1")
      (is (= {:error "bad-format"} (codec/decode-hex 3 1 "01f\r\n")) "CRLF, h = 1: CR where the LF belongs")
      (is (= {:error "bad-format"} (codec/decode-hex 3 2 "01fa\nbc\n")) "right length, LF misplaced")
      (is (= {:error "bad-format"} (codec/decode-hex 3 2 "01f\nabc\nx")) "n+1, not a blank line")
      (is (= {:error "bad-frame-length"} (codec/decode-hex 3 2 "01f\nabc\n\n\n")) "two blank lines"))
    (testing "1x1"
      (is (= {:cells [7]} (codec/decode-hex 1 1 "7\n")))
      (is (= {:cells [7]} (codec/decode-hex 1 1 "7\n\n")))
      (is (= {:error "bad-frame-length"} (codec/decode-hex 1 1 "7")))
      (is (= {:error "bad-frame-length"} (codec/decode-hex 1 1 ""))))
    (testing "the largest grid: 65,792 characters, 65,793 with the blank line"
      (let [t (codec/encode-hex 256 (vec (repeat 65536 0)))]
        (is (= 65792 (count t)))
        (is (= 65536 (count (:cells (codec/decode-hex 256 256 t)))))
        (is (= 65536 (count (:cells (codec/decode-hex 256 256 (str t "\n"))))))
        (is (= {:error "bad-frame-length"} (codec/decode-hex 256 256 (str t "\n\n"))))))))

(deftest decode-is-total
  (doseq [d [nil 42 {} "" "{" '(1 2) [nil] [1.5] :x (byte-array 0) [-1]]]
    (is (contains? #{"bad-frame-length" "bad-format"} (:error (codec/decode 1 1 d))) (pr-str d))))

;; ------------------------------------------------------------ interop

(deftest interop-packets
  (let [blp (codec/encode-blp 2 1 [0 1])
        mcuf (codec/encode-mcuf 2 1 [0 15] 1 15)]
    (is (codec/interop? blp))
    (is (codec/interop? mcuf))
    (is (not (codec/interop? [0 15 0 0])))
    (is (not (codec/interop? (codec/encode-pal16 [15 15] 0xDEAD))) "a prefix is not a magic")
    (is (= [0 15] (codec/interop-cells (codec/parse-interop blp) cga)))
    (is (= [0 15] (codec/interop-cells (codec/parse-interop mcuf) cga)))
    (is (= [8] (codec/interop-cells (codec/parse-interop (codec/encode-mcuf 1 1 [1] 1 2)) cga))
        "1 of maxval 2 is 7.5 of 15, rounded half up")
    (is (= [15] (codec/interop-cells (codec/parse-interop (codec/encode-mcuf 1 1 [255 255 255] 3 255)) cga)))
    (is (= {:error "bad-frame-length"} (codec/parse-interop (pop blp))))
    (is (= {:error "bad-format"} (codec/parse-interop (conj (pop blp) 2))) "BLP value 2")
    (is (= {:error "bad-format"} (codec/parse-interop (codec/encode-mcuf 1 1 [0 0] 2 15))) "2 channels")
    (is (= {:error "bad-format"} (codec/parse-interop (codec/encode-blp 0 1 []))) "grid 0x1")))

;; ------------------------------------------------------------ properties

(defn- grid-cells-gen []
  (gen/bind (gen/tuple (gen/elements [1 2 3 9 16 255 256]) (gen/elements [1 2 3 17]))
            (fn [[w h]] (gen/fmap (fn [cells] [w h cells]) (gen/vector (gen/choose 0 15) (* w h))))))

(def seq-gen (gen/elements [0 1 255 256 65534 65535]))

(deftest round-trips-and-format-symmetry
  (holds "codec/format-symmetry: pal16, pal16+seq, hex, hex+blank decode to the same cells"
         (prop/for-all [[w h cells] (grid-cells-gen) s seq-gen]
           (= {:cells cells}
              (codec/decode-pal16 w h (codec/encode-pal16 cells))
              (dissoc (codec/decode-pal16 w h (codec/encode-pal16 cells s)) :seq)
              (codec/decode-hex w h (codec/encode-hex w cells))
              (codec/decode-hex w h (str (codec/encode-hex w cells) "\n")))))
  (holds "codec/seq: the prefix round-trips"
         (prop/for-all [[w h cells] (grid-cells-gen) s seq-gen]
           (= s (:seq (codec/decode-pal16 w h (codec/encode-pal16 cells s)))))))

(deftest off-by-one-never-decodes
  (holds "codec/length+-1: never decodes; pal16 always bad-frame-length"
         (prop/for-all [[w h cells] (grid-cells-gen)
                        fmt (gen/elements [:pal16 :hex])
                        d (gen/elements [-1 1])
                        s seq-gen
                        pre? gen/boolean]
           (let [data (case fmt
                        :pal16 (if pre? (codec/encode-pal16 cells s) cells)
                        :hex (cond-> (codec/encode-hex w cells) pre? (str "\n")))
                 data (cond
                        (string? data) (if (pos? d) (str data "0") (subs data 1))
                        (pos? d) (conj data 0)
                        :else (pop data))
                 r (codec/decode w h data)]
             (and (:error r) (or (string? data) (= "bad-frame-length" (:error r)))))))
  (holds "codec/out-of-range: one byte 16..255 is bad-format"
         (prop/for-all [[w h cells] (grid-cells-gen) b (gen/choose 16 255) i gen/nat]
           (= {:error "bad-format"} (codec/decode-pal16 w h (assoc cells (rem i (count cells)) b))))))

(deftest decode-total-property
  (holds "codec/decode is total"
         (prop/for-all [d gen/any]
           (map? (codec/decode 2 2 d)))))
