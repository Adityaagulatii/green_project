"The tables of SPEC.md, as data. Nothing here has behaviour.

The shape and glyph art is drawn the way SPEC §4.2 and §8.4 draw it, one
line per box row with the rotations side by side, so the two can be
compared by eye. `#` marks an occupied cell."

(defn freeze [x]
  "Nested lists and dicts, made immutable (lists become tuples)."
  (cond (isinstance x list) (tuple (map freeze x))
        (isinstance x dict) (dfor [k v] (.items x) k (freeze v))
        True x))

;; ------------------------------------------------------------ geometry

(setv ROWS 17  COLS 9           ; the display, §2.1
      HEIGHT 20  WIDTH 11       ; the padded board, §3.1
      SPAWN-ROW 0  SPAWN-COL 3  ; §4.3
      FPS 30
      DCD 2                     ; §5.4: the gate threshold ...
      DCD-PER-FRAME 2)          ; ... and the rise per logical frame

;; ------------------------------------------------------------ colours, pieces, input

(setv PALETTE                   ; §4.1
  {"." #(0 0 0)      "W" #(255 255 255)  "G" #(42 42 42)
   "I" #(0 255 255)  "J" #(0 0 255)      "L" #(255 170 0)  "O" #(255 255 0)
   "S" #(0 255 0)    "Z" #(255 0 0)      "T" #(153 0 255)})

(setv BAG-ORDER #("I" "J" "L" "O" "S" "Z" "T")     ; §9.3, before shuffling
      ACTIONS #("left" "right" "soft_drop" "hard_drop"
                "rotate_cw" "rotate_ccw" "rotate_180" "hold"))

;; §4.2. Rotation r+1 is clockwise from r.
(setv SHAPE-ART
  {"I" ["....  ..#.  ....  .#.."
        "####  ..#.  ....  .#.."
        "....  ..#.  ####  .#.."
        "....  ..#.  ....  .#.."]
   "J" ["....  ....  ....  ...."
        ".#..  ..##  ....  ..#."
        ".###  ..#.  .###  ..#."
        "....  ..#.  ...#  .##."]
   "L" ["....  ....  ....  ...."
        "...#  ..#.  ....  .##."
        ".###  ..#.  .###  ..#."
        "....  ..##  .#..  ..#."]
   "O" ["....  ....  ....  ...."
        ".##.  ....  ....  ##.."
        ".##.  .##.  ##..  ##.."
        "....  .##.  ##..  ...."]
   "S" ["....  ....  ....  ...."
        "..##  ..#.  ....  .#.."
        ".##.  ..##  ..##  .##."
        "....  ...#  .##.  ..#."]
   "Z" ["....  ....  ....  ...."
        ".##.  ...#  ....  ..#."
        "..##  ..##  .##.  .##."
        "....  ..#.  ..##  .#.."]
   "T" ["....  ....  ....  ...."
        "..#.  ..#.  ....  ..#."
        ".###  ..##  .###  .##."
        "....  ..#.  ..#.  ..#."]})

(defn boxes [art]
  "The four boxes drawn side by side in `art`, each a list of 4 rows."
  (lfor rot (range 4) (lfor line art (get (.split line) rot))))

(setv CELLS                     ; CELLS[shape][rot]: occupied #(i j), row-major
  (dfor [shape art] (.items SHAPE-ART)
        shape (tuple (gfor box (boxes art)
                           (tuple (gfor [i row] (enumerate box)
                                        [j ch] (enumerate row)
                                        :if (= ch "#")
                                        #(i j)))))))

;; ------------------------------------------------------------ kicks, §5.3
;; [state][test] → [x y], with y pointing UP.

(setv ZERO5 [[0 0] [0 0] [0 0] [0 0] [0 0]]
      O-OFFSETS [ZERO5
                 [[0 -1] [0 -1] [0 -1] [0 -1] [0 -1]]
                 [[-1 -1] [-1 -1] [-1 -1] [-1 -1] [-1 -1]]
                 [[-1 0] [-1 0] [-1 0] [-1 0] [-1 0]]])

(setv KICKS
  (freeze (| (dfor shape "JLSTZ"
                   shape [ZERO5
                          [[0 0] [1 0] [1 -1] [0 2] [1 2]]
                          ZERO5
                          [[0 0] [-1 0] [-1 -1] [0 2] [-1 2]]])
             {"I" [[[0 0] [-1 0] [2 0] [-1 0] [2 0]]
                   [[0 0] [1 0] [1 0] [1 1] [1 -2]]
                   [[0 0] [2 0] [-1 0] [2 -1] [-1 -1]]
                   [[0 0] [0 0] [0 0] [0 -2] [0 1]]]
              "O" O-OFFSETS})))

;; QUIRK-1: the 180° *source* offsets; the target offsets still come from KICKS.
(setv KICKS-180
  (freeze (| (dfor shape "JLSTZ"
                   shape [[[0 0] [0 1] [1 0] [-1 0] [0 -1]]
                          [[0 0] [1 0] [0 1] [0 -1] [-1 0]]
                          ZERO5
                          ZERO5])
             {"I" [[[0 0] [0 0] [-1 0] [2 0] [-1 1]]
                   [[0 0] [0 0] [0 1] [0 -2] [1 1]]
                   ZERO5
                   ZERO5]
              "O" O-OFFSETS})))

;; ------------------------------------------------------------ gravity, §7.2
;; GRAVITY[L] = 1 / GRAVITY-DEN[min(L, 29)]

(setv GRAVITY-DEN (+ #(48 43 38 33 28 23 18 13 8 6)
                     (* #(5) 3) (* #(4) 3) (* #(3) 3) (* #(2) 10) #(1)))

;; ------------------------------------------------------------ animations, §8

(setv FLASH-FRAMES 5
      FILL-FRAMES 34  WAIT-FRAMES 150  FALL-FRAMES 34
      GAMEOVER-FRAMES (+ FILL-FRAMES WAIT-FRAMES FALL-FRAMES)   ; 218
      DIGIT-FRAMES 30
      COUNTDOWN-FRAMES (* 3 DIGIT-FRAMES))                       ; 90

;; §8.4: rows 3–12 of the glyphs "3", "2" and "1". Rows 0–2 and 13–16 are
;; black; the legacy's extra rows 17–18 are dropped (QUIRK-9).
(setv GLYPH-ART
  ["..#####..  ..#####..  ....##..."
   "..#####..  ..#####..  ....##..."
   ".....##..  .....##..  ....##..."
   ".....##..  .....##..  ....##..."
   "..#####..  ..#####..  ....##..."
   "..#####..  ..#####..  ....##..."
   ".....##..  ..##.....  ....##..."
   ".....##..  ..##.....  ....##..."
   "..#####..  ..#####..  ....##..."
   "..#####..  ..#####..  ....##..."])

(setv GLYPHS
  (dfor [k digit] (enumerate "321")
        digit (+ (* #((* "." COLS)) 3)
                 (tuple (gfor line GLYPH-ART (.replace (get (.split line) k) "#" "W")))
                 (* #((* "." COLS)) 4))))
