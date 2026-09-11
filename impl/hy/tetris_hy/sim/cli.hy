"Run the Hy engine headless, and show it.

    hy -m tetris_hy.sim --seed 42 --bot --frames 300 --ansi
    hy -m tetris_hy.sim --trace spec/conformance/traces/08-hold.json --ansi-final
    hy -m tetris_hy.sim --building

Prints a JSON summary of the final state (the §12 observation without the
frame bytes) when it is done."

(import argparse json sys)
(import tetris_hy [SPEC-VERSION])
(import tetris_hy.conformance [observe events-by-frame make-trace])
(import tetris_hy.sim.recorder [record tetris])
(import tetris_hy.sim.bot [make-bot])
(import tetris_hy.sim.ansi [play ansi-frame])
(import tetris_hy.sim.building [GREEN-BUILDING windows])

(defn replay [events]
  "A controller that plays [frame action down] events back, frame by frame."
  (setv inputs (events-by-frame events)
        k 0)
  (defn controller [_state]
    (nonlocal k)
    (setv now k  k (+ k 1))
    (.get inputs now []))
  controller)

(defn parser []
  (setv p (argparse.ArgumentParser
            :prog "tetris_hy.sim" :description __doc__
            :formatter-class argparse.RawDescriptionHelpFormatter))
  (.add-argument p "--seed" :type int :default 1)
  (.add-argument p "--frames" :type int :default None
                 :help "frames to run (default 900, or the trace's length)")
  (.add-argument p "--bot" :action "store_true" :help "a watchable auto-player")
  (.add-argument p "--bot-fast" :action "store_true" :help "an auto-player that acts every frame")
  (.add-argument p "--trace" :help "replay the seed and events of a conformance trace")
  (.add-argument p "--ansi" :action "store_true" :help "animate in the terminal")
  (.add-argument p "--ansi-final" :action "store_true" :help "print the last frame")
  (.add-argument p "--plain" :action "store_true" :help "a bare grid, without floors and mullions")
  (.add-argument p "--speed" :type float :default 1.0 :help "--ansi playback speed (0: no delay)")
  (.add-argument p "--trace-out" :help "save the run as a trace")
  (.add-argument p "--building" :action "store_true"
                 :help "print the (provisional) window → floor/bay mapping and exit")
  p)

(defn main [[argv None]]
  (setv args (.parse-args (parser) argv)
        seed args.seed
        frames (or args.frames 900)
        controller None)
  (when args.building
    (print (json.dumps {"building" GREEN-BUILDING "windows" (windows)}))
    (return 0))
  (cond
    args.trace
      (with [fh (open args.trace)]
        (setv trace (json.load fh)
              seed (get trace "seed")
              frames (or args.frames (get trace "frames"))
              controller (replay (get trace "events"))))
    args.bot-fast
      (setv controller (make-bot))
    args.bot
      (setv controller (make-bot :pace 3 :think 6 :batch False)))
  (setv [rec state] (record (tetris seed) frames controller))
  (when args.ansi
    (defn caption [i]
      (setv h (get rec.info i))
      (.format "frame {}  score {}  level {}  {}"
               i (get h "score") (get h "level") (get h "phase")))
    (play rec.frames :speed args.speed :caption caption :facade (not args.plain)))
  (when args.ansi-final
    (print (ansi-frame (get rec.frames -1) :facade (not args.plain))))
  (when args.trace-out
    (with [fh (open args.trace-out "w")]
      (json.dump (make-trace "sim-run" "recorded by tetris_hy.sim" seed frames
                             rec.events)
                 fh :indent 1)
      (.write fh "\n")))
  (setv summary (observe state))
  (del (get summary "frame_hex"))
  (print (json.dumps summary :sort-keys True))
  0)
