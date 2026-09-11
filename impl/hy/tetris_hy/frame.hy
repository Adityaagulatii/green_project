"What the facade shows (SPEC §8), and frame digests (§9.4).

Frames are handled as 17 strings of 9 cell codes (the palette keys of
§4.1) until the last moment, when `render` turns them into RGB."

(require tetris_hy.macros [->])
(import hashlib)
(import tetris_hy.tables [ROWS COLS PALETTE GLYPHS FILL-FRAMES WAIT-FRAMES
                          DIGIT-FRAMES COUNTDOWN-FRAMES])
(import tetris_hy.engine [cells landed paint])

(setv BLACK (* #((* "." COLS)) ROWS)
      WHITE (* #((* "W" COLS)) ROWS))

(defn crop [board]
  "The display part of a padded board, §3.3."
  (tuple (gfor row (cut board 1 (+ ROWS 1)) (cut row 1 (+ COLS 1)))))

(defn compose [board piece]
  "§8.1: the board, then the gray ghost, then the active piece; cropped."
  (crop (-> board
            (paint (cells (landed board piece)) "G")
            (paint (cells piece) (get piece 0)))))

(defn game-over-codes [over-board t]
  "§8.3: fill up from the bottom, wait all white, then go dark from the
  top (QUIRK-19)."
  (cond
    (< t FILL-FRAMES)
      (let [first-white (- ROWS (// t 2))]          ; rows 17−k … 17 are white
        (tuple (gfor r (range 1 (+ ROWS 1))
                     (if (>= r first-white)
                         (* "W" COLS)
                         (cut (get over-board r) 1 (+ COLS 1))))))
    (< t (+ FILL-FRAMES WAIT-FRAMES))
      WHITE
    True
      (let [last-dark (+ 1 (// (- t FILL-FRAMES WAIT-FRAMES) 2))]   ; rows 1 … 1+k are black
        (tuple (gfor r (range 1 (+ ROWS 1))
                     (if (<= r last-dark) (* "." COLS) (* "W" COLS)))))))

(defn render-codes [s]
  "The frame shown for state `s`, as 17 strings of 9 cell codes."
  (if (not (:script s))
      (compose (:board s) (:active s))
      (let [kind (get (:script s) 0 0)
            t (:t s)]
        (match kind
          "countdown" (if (<= 0 t (- COUNTDOWN-FRAMES 1))
                          (get GLYPHS (get "321" (// t DIGIT-FRAMES)))
                          BLACK)
          "black" BLACK
          "flash" (crop (:flash s))
          "gameover" (game-over-codes (:over-board s) t)))))

(defn codes->rgb [codes]
  (tuple (gfor row codes (tuple (gfor ch row (get PALETTE ch))))))

(defn render [s]
  "The display Frame: 17 rows × 9 columns of #(r g b) (§2.1)."
  (codes->rgb (render-codes s)))

(defn frame-bytes [codes]
  "The canonical 459 bytes: row-major, then R G B per cell (§9.4)."
  (bytes (gfor row codes  ch row  v (get PALETTE ch)  v)))

(defn rgb-bytes [frame]
  (bytes (gfor row frame  rgb row  v rgb  v)))

(defn digest [codes]
  "The SHA-256 digest of a frame given as cell codes."
  (.hexdigest (hashlib.sha256 (frame-bytes codes))))

(defn rgb-digest [frame]
  "The SHA-256 digest of an RGB frame."
  (.hexdigest (hashlib.sha256 (rgb-bytes frame))))
