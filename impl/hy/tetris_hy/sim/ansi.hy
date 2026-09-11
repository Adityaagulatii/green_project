"The ANSI truecolor terminal renderer.

With `facade` (the default in the CLI), each row is labelled with its
floor from the building model, and the windows are separated by gray
mullions, so the terminal looks like the side of the building."

(import sys time)
(import tetris_hy.sim.building [GREEN-BUILDING lit-floors])

(setv RESET "\x1b[0m"
      HIDE-CURSOR "\x1b[?25l"
      SHOW-CURSOR "\x1b[?25h"
      HOME "\x1b[H"
      CLEAR "\x1b[2J"
      ERASE-LINE "\x1b[K"
      MULLION #(64 64 64))

(defn bg [rgb]
  "The escape sequence for a 24-bit background colour."
  (setv [r g b] rgb)
  f"\x1b[48;2;{r};{g};{b}m")

(defn frame-lines [frame [facade False] [building GREEN-BUILDING]]
  "One frame as 17 lines of coloured cells."
  (if facade
      (lfor [floor row] (zip (lit-floors building) frame)
            (+ "F" (.rjust (str floor) 2) " "
               (.join (+ (bg MULLION) " ") (gfor rgb row (+ (bg rgb) "  ")))
               RESET))
      (lfor row frame
            (+ (.join "" (gfor rgb row (+ (bg rgb) "  "))) RESET))))

(defn ansi-frame [frame #** options]
  (.join "\n" (frame-lines frame #** options)))

(defn play [frames [out None] [fps 30] [speed 1.0] [sleep time.sleep]
            [caption None] [facade False]]
  "Animate `frames` in place at fps × speed; speed 0 draws as fast as it can."
  (setv out (or out sys.stdout)
        period (if (> speed 0) (/ 1.0 (* fps speed)) 0.0))
  (.write out (+ HIDE-CURSOR CLEAR))
  (try
    (for [[i frame] (enumerate frames)]
      (.write out (+ HOME (ansi-frame frame :facade facade) "\n"
                     (if caption (caption i) f"frame {i}") ERASE-LINE "\n"))
      (.flush out)
      (when period (sleep period)))
    (finally
      (.write out SHOW-CURSOR)
      (.flush out))))
