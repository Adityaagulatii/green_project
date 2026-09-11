"The spec PRNG and the 7-bag (SPEC §9.3)."

(import tetris_hy.tables [BAG-ORDER])

(setv MASK32 0xFFFFFFFF)

(defn seed-state [seed]
  "seed mod 2^32, or 0x9E3779B9 when that is 0 (xorshift must not start at 0)."
  (or (& (int seed) MASK32) 0x9E3779B9))

(defn xorshift32 [x]
  "Marsaglia's xorshift32 (13, 17, 5). The new state is also the output."
  (setv x (^ x (& (<< x 13) MASK32))
        x (^ x (>> x 17))
        x (^ x (& (<< x 5) MASK32)))
  x)

(defn shuffle-bag [x]
  "A Fisher–Yates shuffle of BAG-ORDER. Returns #(bag x'), having used 6 outputs."
  (setv bag (list BAG-ORDER))
  (for [i (range 6 0 -1)]
    (setv x (xorshift32 x)
          j (% x (+ i 1))
          [(get bag i) (get bag j)] [(get bag j) (get bag i)]))
  #((tuple bag) x))
