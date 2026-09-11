"P19: the known answers of SPEC Appendix A. Also the Hy tables against
the Python reference's, which impl/python/tests/test_kat.py checks against
the legacy itself, and the binary64 cadence of experiment 003."

(import pytest)
(import tetris_hy [tables :as hyt])
(import tetris_engine [tables :as pyt])
(import tetris_hy.prng [seed-state xorshift32 shuffle-bag])
(import tetris_hy.engine [init level-target gravity-step])
(import tetris_hy.frame [digest rgb-digest render BLACK WHITE])

(setv XORSHIFT {1  [270369 67634689 2647435461 307599695 2398689233]
                42 [11355432 2836018348 476557059 3648046016 3759983556]
                0  [1359758873 3761132862 2075758394 25405621 3862129951]}
      BAGS {1  ["SILOZTJ" "LJOZSTI" "ZIOJSLT"]
            42 ["JLOIZTS" "ILJOZST" "OZITJLS"]
            0  ["ZLOJTIS" "ZOTSIJL" "JITOLSZ"]}
      BLACK-DIGEST "e0ee29ce7978a33861e6e63545deda9e734ea784ee8e4ba6fd6aa56b775f6ca9"
      WHITE-DIGEST "cc1c8c603a0863247abc4b8a117a234714b37d2be10ca27218301e5e617830d8"
      CADENCE {48 25  43 22  38 20  33 17  28 15  23 12  18 9  13 7  8 4
               6 3  5 3  4 2  3 2  2 1  1 1})

(defn [(pytest.mark.parametrize "seed" [0 1 42])] test-p19-xorshift-vectors [seed]
  (setv x (seed-state seed)
        out [])
  (for [_ (range 5)]
    (setv x (xorshift32 x))
    (.append out x))
  (assert (= out (get XORSHIFT seed))))

(defn [(pytest.mark.parametrize "seed" [0 1 42])] test-p19-bag-vectors [seed]
  (setv x (seed-state seed)
        bags [])
  (for [_ (range 3)]
    (setv [bag x] (shuffle-bag x))
    (.append bags (.join "" bag)))
  (assert (= bags (get BAGS seed)))
  (assert (= (get (:active (init seed)) 0) (get BAGS seed 0 0))))

(defn test-p19-digest-vectors []
  (assert (= (digest BLACK) BLACK-DIGEST))
  (assert (= (digest WHITE) WHITE-DIGEST))
  (assert (= (rgb-digest (render (init 5))) BLACK-DIGEST)))   ; before frame 0

(defn test-seed-zero-and-wraparound []
  (assert (= (seed-state 0) (seed-state (** 2 32)) 0x9E3779B9))
  (assert (= (seed-state (+ (** 2 32) 7)) 7)))

(defn test-gravity-cadence-is-binary64 []
  "Experiment 003: level 0 drops every 25 frames, not 24."
  (for [[den frames] (.items CADENCE)]
    (setv level (.index hyt.GRAVITY-DEN den)
          acc 0.0
          n 0)
    (while (< acc 1.0)
      (setv acc (+ acc (gravity-step level))
            n (+ n 1)))
    (assert (= n frames) den)))

(defn test-level-target-is-level-plus-five []
  (for [level (range 500)]
    (assert (= (level-target level) (+ level 5)))))

(defn test-tables-match-the-python-reference []
  (assert (= hyt.CELLS pyt.CELLS))
  (assert (= hyt.KICKS pyt.KICKS))
  (assert (= hyt.KICKS-180 pyt.KICKS-180))
  (assert (= hyt.GRAVITY-DEN pyt.GRAVITY-DEN))
  (assert (= hyt.PALETTE pyt.PALETTE))
  (assert (= hyt.GLYPHS pyt.COUNTDOWN))
  (assert (= hyt.BAG-ORDER pyt.BAG-ORDER))
  (assert (= hyt.ACTIONS pyt.ACTIONS))
  (assert (= #(hyt.SPAWN-ROW hyt.SPAWN-COL) #(pyt.SPAWN-ROW pyt.SPAWN-COL)))
  (assert (= #(hyt.FLASH-FRAMES hyt.GAMEOVER-FRAMES hyt.COUNTDOWN-FRAMES)
             #(pyt.CLEAR-FLASH-FRAMES pyt.GAMEOVER-FRAMES pyt.COUNTDOWN-FRAMES))))
