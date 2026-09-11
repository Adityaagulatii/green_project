(ns tetris.displays.core
  "The sink's fold (wal.sh/tools/display v0.2.1, section 8, Reduction
  contract), compatible with the user's own core.cljc:

    (reduce-event state event) -> state, total over every event kind
    (dirty state prev)         -> [[x y idx] ...], the changed cells

  Events are the control messages as maps with string :op (\"caps\",
  \"lease\", \"error\"; JSON keys keywordized), plus {:op :frame :data d},
  where d is a string (a hex frame) or octets (a pal16 frame), {:op :open},
  {:op :close} and {:op :tick :now unix-seconds}. Anything else, and a
  named event that is malformed, is unknown input: state unchanged but
  :dropped increments.

  Choices where the table is silent, the same as the contract's Python
  reference (contract/display_contract.py) unless marked OURS:
  - a control message is malformed when it fails its schema
    (contract/schemas): caps needs display, w, h, fps, format and a
    16-entry palette; lease needs display, holder and expires (either may
    be null); error needs one of the five reasons. Integral floats count as
    integers (w 9.0).
  - a frame must match the state's :format: pal16 takes octets, hex a
    string; the other carrier is dropped. A pal16 sequence prefix is
    stripped and not checked (reordering is the relay's drop).
  - a caps that contradicts a fixed grid is refused whole: status :error,
    :error {:reason \"bad-src\" :caps {:w :h} :fixed {:w :h}}, nothing else.
  - :tick marks :idle when expires <= now.
  - OURS: `init` defaults to green-building (the reference: cga40), and
    rejects an out-of-domain URL parameter as the page does (section 3)."
  (:require [clojure.spec.alpha :as s]
            [clojure.spec.gen.alpha :as gen]
            [tetris.displays.caps :as caps]
            [tetris.displays.codec :as codec]
            [tetris.displays.specs :as ds]))

(def max-cells "Spec section 3: w * h must not exceed 65,536." 65536)

(defn blank
  "w*h cells of index 0."
  [w h]
  (vec (repeat (* w h) 0)))
(s/fdef blank :args (s/cat :w ::ds/w :h ::ds/h) :ret ::ds/cells
        :fn #(= (count (:ret %)) (* (-> % :args :w) (-> % :args :h))))

(defn integral
  "x as a long when it is an integer, or a finite float with no fraction
  (JSON has one number type); else nil."
  [x]
  (cond
    (int? x) x
    (and (number? x)
         #?(:clj (Double/isFinite (double x)) :cljs (js/isFinite x))
         (== x (Math/floor (double x))))
    (long x)
    :else nil))
(s/fdef integral :args (s/cat :x any?) :ret (s/nilable int?))

(defn grid-ok?
  "w and h integers in 1..256 with w*h at most 65,536 (which can never bind
  alone: 256*256 is 65,536)."
  [w h]
  (let [w (integral w) h (integral h)]
    (boolean (and w h (<= 1 w 256) (<= 1 h 256) (<= (* w h) max-cells)))))
(s/fdef grid-ok? :args (s/cat :w any? :h any?) :ret boolean?)

;; ------------------------------------------------------------ state

(s/def ::fixed? boolean?)
(s/def ::format #{:pal16 :hex})
(s/def ::palette (s/or :named keyword? :announced ::ds/palette16))
(s/def ::status #{:connecting :live :idle :closed :error})
(s/def ::holder (s/nilable string?))
(s/def ::expires (s/nilable number?))
(s/def ::seq nat-int?)
(s/def ::dropped nat-int?)
(s/def ::error any?)

(declare init reduce-event event-gen)

(defn- state-gen []
  (gen/fmap (fn [[d evs]] (reduce reduce-event (init {:d d}) evs))
            (gen/tuple (gen/elements ["green-building" "trs80" "blinkenlights"])
                       (gen/vector (event-gen) 0 12))))

(s/def ::state
  (s/with-gen
    (s/and (s/nonconforming
            (s/keys :req-un [::ds/w ::ds/h ::fixed? ::format ::palette ::ds/fps ::status
                             ::holder ::expires ::seq ::ds/cells ::dropped ::error]))
           #(= (count (:cells %)) (* (:w %) (:h %))))
    state-gen))

(s/def ::d (set caps/display-names))
(s/def ::params (s/keys :opt-un [::d ::ds/w ::ds/h ::ds/fps]))

(defn init
  "The canonical state for URL parameters {:d :w :h :fps} (all optional):
  preset `d` (default green-building), explicit :w / :h fix the grid, :fps
  lowers the preset's rate (never raises it). A parameter outside its
  domain is rejected, not clamped (spec section 3): status :error with
  {:reason \"bad-preset\" | \"bad-w\" | \"bad-h\" | \"bad-fps\" :param ...}, on
  the preset's grid."
  ([] (init {}))
  ([{:keys [d w h fps]}]
   (let [d (or d caps/default-display)
         p (or (caps/preset d) (caps/preset caps/default-display))
         iw (integral w) ih (integral h) ifps (integral fps)
         bad (cond
               (nil? (caps/preset d)) "d"
               (and (some? w) (not (and iw (<= 1 iw 256)))) "w"
               (and (some? h) (not (and ih (<= 1 ih 256)))) "h"
               (and (some? fps) (not (and ifps (<= 1 ifps 60)))) "fps")
         gw (if (and iw (not bad)) iw (:w p))
         gh (if (and ih (not bad)) ih (:h p))]
     {:w gw :h gh
      :fixed? (boolean (and (not bad) (or iw ih)))
      :format (keyword (:format p))
      :palette (keyword (:palette p))
      :fps (if (and ifps (not bad)) (min ifps (:fps p)) (:fps p))
      :status (if bad :error :connecting)
      :holder nil :expires nil
      :seq 0 :cells (blank gw gh)
      :dropped 0
      :error (when bad {:reason (str "bad-" (if (= bad "d") "preset" bad)) :param bad})})))
(s/fdef init :args (s/? (s/cat :params (s/nilable map?))) :ret ::state)

;; ------------------------------------------------------------ message checks

(defn- nonblank? [x] (and (string? x) (pos? (count x))))

(defn caps-ok?
  "Does a caps message pass contract/schemas/caps.json (plus w*h <= 65,536)?"
  [{:keys [display w h fps format palette]}]
  (let [f (integral fps)]
    (boolean (and (nonblank? display) (grid-ok? w h) f (<= 1 f 60)
                  (contains? #{"pal16" "hex"} format) (s/valid? ::ds/palette16 palette)))))
(s/fdef caps-ok? :args (s/cat :msg map?) :ret boolean?)

(defn lease-ok?
  "Does a lease message pass contract/schemas/lease.json?"
  [{:keys [display holder expires] :as msg}]
  (boolean (and (nonblank? display) (contains? msg :holder) (contains? msg :expires)
                (or (nil? holder) (string? holder))
                (or (nil? expires) (some-> (integral expires) (>= 0))))))
(s/fdef lease-ok? :args (s/cat :msg map?) :ret boolean?)

;; ------------------------------------------------------------ reductions

(defn- drop-one [state] (update state :dropped inc))

(defn- on-caps [state {:keys [w h fps format palette] :as ev}]
  (let [w (integral w) h (integral h)]
    (cond
      (not (caps-ok? ev)) (drop-one state)

      (and (:fixed? state) (not= [w h] [(:w state) (:h state)]))
      (assoc state :status :error
             :error {:reason "bad-src" :caps {:w w :h h} :fixed {:w (:w state) :h (:h state)}})

      :else
      (assoc state :w w :h h :cells (blank w h) :fps (integral fps)
             :format (keyword format) :palette (vec palette)))))

(defn- on-lease [state {:keys [holder expires] :as ev}]
  (if (lease-ok? ev)
    (cond-> (assoc state :holder holder :expires (some-> expires integral))
      (nil? holder) (assoc :status :idle))
    (drop-one state)))

(defn- on-frame [state {:keys [data]}]
  (let [{:keys [w h format]} state
        r (cond
            (and (= format :pal16) (not (string? data)) (codec/octets data)) (codec/decode-pal16 w h data)
            (and (= format :hex) (string? data)) (codec/decode-hex w h data))]
    (if-let [cells (:cells r)]
      (-> state (assoc :cells cells :status :live) (update :seq inc))
      (drop-one state))))

(defn- on-tick [state {:keys [now]}]
  (if (and (number? now) (some? (integral (Math/floor (double now)))))
    (let [exp (:expires state)]
      (cond-> state (and (number? exp) (>= now exp)) (assoc :status :idle)))
    (drop-one state)))

(defn reduce-event
  "One event -> the next sink state (see the ns doc). Total: every input
  gives a state, and unknown input counts as dropped."
  [state event]
  (case (when (map? event) (:op event))
    :open (assoc state :status :connecting)
    "caps" (on-caps state event)
    "lease" (on-lease state event)
    :frame (on-frame state event)
    "error" (if (contains? ds/reasons (:reason event))
              (assoc state :error {:reason (:reason event)})
              (drop-one state))
    :close (assoc state :status :closed)
    :tick (on-tick state event)
    (drop-one state)))
(s/fdef reduce-event
  :args (s/cat :state ::state :event any?)
  :ret ::state
  :fn (fn [{{:keys [state]} :args ret :ret}]
        (and (every? #(<= 0 % 15) (:cells ret))
             (<= (:seq state) (:seq ret) (inc (:seq state)))
             (<= (:dropped state) (:dropped ret) (inc (:dropped state))))))

(defn reduce-events
  "Fold a message log into a sink state."
  [state events]
  (reduce reduce-event state events))
(s/fdef reduce-events :args (s/cat :state ::state :events (s/coll-of any? :max-count 20)) :ret ::state)

;; ------------------------------------------------------------ projection

(defn dirty
  "The [x y idx] of every cell of `state` that differs from `prev`, in
  row-major order; every cell when there is no prev or the grid changed.
  Lossy by declaration: it keeps only what the DOM needs."
  [state prev]
  (let [{:keys [w cells]} state
        pc (:cells prev)
        same? (and prev (= w (:w prev)) (= (:h state) (:h prev)) (= (count cells) (count pc)))]
    (into [] (keep-indexed (fn [i c] (when (or (not same?) (not= c (nth pc i)))
                                       [(rem i w) (quot i w) c])))
          cells)))
(s/fdef dirty
  :args (s/cat :state ::state :prev (s/nilable ::state))
  :ret (s/coll-of (s/tuple nat-int? nat-int? ::ds/idx) :kind vector?)
  :fn (fn [{{:keys [state prev]} :args ret :ret}]
        (if (and prev (= [(:w state) (:h state)] [(:w prev) (:h prev)]))
          (= (:cells state)
             (reduce (fn [cs [x y c]] (assoc cs (+ x (* y (:w state))) c)) (:cells prev) ret))
          (= (count ret) (count (:cells state))))))

;; ------------------------------------------------------------ generators

(def boundary-grids
  "Grids at the edges of the domain, and a few ordinary ones."
  [[1 1] [1 256] [256 1] [9 17] [3 2] [2 3]])

(def frame-faults
  "Ways frame-gen breaks (or legally varies) a frame."
  [:seq :short :long :seq-short :seq-long :byte16 :byte255 :crlf :upper :other-carrier])

(defn frame-data
  "The frame payload for `cells` of a grid `w` wide in `fmt` (:pal16 or
  :hex), with `fault` (:none or one of `frame-faults`) and sequence `s`."
  [w cells fmt fault s]
  (let [n (count cells)
        hex (codec/encode-hex w cells)]
    (case [fmt fault]
      [:pal16 :none] cells
      [:pal16 :seq] (codec/encode-pal16 cells s)
      [:pal16 :short] (pop cells)
      [:pal16 :long] (conj cells 0)
      [:pal16 :seq-short] (pop (codec/encode-pal16 cells s))
      [:pal16 :seq-long] (conj (codec/encode-pal16 cells s) 0)
      [:pal16 :byte16] (assoc cells (rem s n) 16)
      [:pal16 :byte255] (assoc cells (rem s n) 255)
      [:pal16 :crlf] (conj cells 0 0 0)
      [:pal16 :upper] cells
      [:pal16 :other-carrier] hex
      [:hex :none] hex
      [:hex :seq] (str hex "\n")
      [:hex :short] (subs hex 1)
      [:hex :long] (str "0" hex)
      [:hex :seq-short] (subs hex 0 (dec (count hex)))
      [:hex :seq-long] (str hex "\n\n")
      [:hex :byte16] (str "g" (subs hex 1))
      [:hex :byte255] (str "G" (subs hex 1))
      [:hex :crlf] (apply str (mapcat (fn [c] (if (= \newline c) [\return \newline] [c])) hex))
      [:hex :upper] (.toUpperCase ^String hex)
      [:hex :other-carrier] cells)))

(defn frame-gen
  "A frame event for a grid [w h]: valid in either format, or varied or
  broken at a boundary (see `frame-faults`)."
  [[w h]]
  (gen/fmap
   (fn [[cells fmt fault s]] {:op :frame :data (frame-data w cells fmt fault s)})
   (gen/tuple (gen/vector (gen/choose 0 15) (* w h))
              (gen/elements [:pal16 :hex])
              (gen/frequency [[4 (gen/return :none)] [3 (gen/elements frame-faults)]])
              (gen/elements [0 1 255 256 65534 65535]))))

(defn event-gen
  "Sink events: caps (boundary grids, and 0 / 257 which are refused),
  lease, frames for those grids (valid or broken), error, open, close, tick,
  and arbitrary values."
  []
  (let [grid (gen/elements boundary-grids)]
    (gen/frequency
     [[3 (gen/fmap (fn [[[w h] fps fmt]] {:op "caps" :display "x" :w w :h h :fps fps :format fmt
                                          :palette (codec/palette16 (caps/palette "cga"))})
                   (gen/tuple (gen/one-of [grid (gen/elements [[0 1] [257 1] [1 257] [256 256] [9.0 17.0]])])
                              (gen/elements [1 30 60 61 0])
                              (gen/elements ["pal16" "hex" "hex" "rgb24"])))]
      [2 (gen/fmap (fn [[h e]] {:op "lease" :display "x" :holder h :expires e})
                   (gen/tuple (gen/elements [nil "emacs@minibos" "bb" 7]) (gen/elements [nil 0 100 2000000000 -1])))]
      [6 (gen/bind grid frame-gen)]
      [1 (gen/fmap (fn [r] {:op "error" :reason r}) (gen/elements (conj (vec ds/reasons) "nope")))]
      [1 (gen/elements [{:op :open} {:op :close} {:op :tick :now 50} {:op :tick :now 2e9}
                        {:op :tick} {:op "busy"} {:op :frame} {:op :frame :data {}}
                        {:op "lease" :holder nil}])]
      [1 (gen/any-printable)]])))
