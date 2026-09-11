(ns tetris.displays.lease
  "The wal.sh/tools/display v0.2.1 relay as a pure fold: the timed-reservation
  machine free -> held(holder, expires) -> free, one per display, plus the
  frame rules (format, sequence, fps) and the viewer fan-out.

  Vocabulary (state-machine-ladder, spec-v2.4.0; timed-reservation rung,
  proposal 2.9.0): the append-only LOG of events is the source of truth,
  (fold config log) is the pure derivation, (view state) the projection,
  never authoritative. There is no clock argument: every event carries its
  event time :ts (epoch ms), and expiry is an event the SCHEDULER writes into
  the log ({:event :expire}), asked for by an :arm effect. The fold alone
  decides whether that expiry is live or stale (the lease was renewed or
  replaced since: re-arm, or ignore). `run` plays the scheduler for tests;
  the bb relay's timer plays it for real.

    (step state event) -> [state' effects]

  Log events (:ts on all; :conn on connection events):
    {:event :open    :conn c}
    {:event :close   :conn c}
    {:event :control :conn c :msg {...}}   a JSON text message, keys keywordized
    {:event :control :conn c :malformed true}  text starting with { that is not a JSON object
    {:event :frame   :conn c :data d}      d: string (hex text) or octets (binary)
    {:event :expire  :display d :lease id} written by the scheduler

  Effects:
    {:fx :send  :to c :msg {...}}          one JSON text message
    {:fx :frame :to c :data d}             one frame in the display's fan-out
                                           format: octets (pal16) or text (hex)
    {:fx :close :to c :code 1013 :reason s}  close the socket (viewer cap)
    {:fx :arm   :display d :lease id :at ms}  write an :expire event at ms

  Gates, in ladder order: the schema gate (an entry that is not a
  ::log-entry is an anomaly and changes nothing), then I6 (an entry older
  than the watermark is recorded, then applied), then T2 (an event on a
  connection that is not open is an anomaly and changes nothing).

  Where v0.2.1 is silent this follows the contract's reference relay
  (contrib/displays/demo/relay.py on contract/display_contract.py): each
  rule lives in one place (`reason-for`, `message-ok?`, `ttl-ms`,
  `reordered?`, `too-fast?`). Release and close end the lease with `lease`
  null to the viewers; only expiry adds the black frame, and the holder is
  not told. A connection holds at most one lease: reserving another display
  releases the first. A reserve by the holder grants a new lease (new id,
  rate and sequence reset). A renewal (frame or renew) sends the viewers a
  fresh `lease` whenever the whole-second expires changes, so a viewer's
  :tick never marks a live display idle."
  (:require [clojure.spec.alpha :as s]
            [clojure.spec.gen.alpha :as gen]
            [tetris.displays.caps :as caps]
            [tetris.displays.codec :as codec]
            [tetris.displays.specs :as ds]))

(def max-ttl-s "Spec section 6.3: ttl is at most 900 s." 900)
(def default-ttl-s "ttl when a reserve names none (the spec's example value)." 300)
(def max-viewers "Spec section 10: the relay-side cap per display." 32)
(def viewer-cap-close "WebSocket close code for a viewer over the cap: try again later." 1013)
(def default-rate-tolerance "The fps rule's jitter allowance, in thousandths of a frame period." 200)
(def source-formats #{"pal16" "hex" "rgb24"})
(def ops #{"view" "reserve" "renew" "release"})

(def reason-for
  "Every refusal the relay makes -> one of the five v0.2.1 reasons. The
  first five are the spec's own; the rest are cases it names no reason
  for, answered as the reference relay does, kept here so a fixture can
  move any of them in one edit."
  {:not-holder "not-holder"             ; frame, renew or release from a non-holder
   :bad-frame-length "bad-frame-length" ; frame of the wrong length
   :bad-format "bad-format"             ; byte 16..255, a non-hex digit, a bad packet
   :rate "rate"                         ; faster than fps, or a lower sequence
   :unknown-op "unknown-op"             ; op not view/reserve/renew/release
   :bad-json "bad-format"               ; text starting with { that is not a JSON object
   :no-op "bad-format"                  ; a JSON object without a string op
   :bad-message "bad-format"            ; a known op failing its schema (contract/schemas)
   :unknown-display "bad-format"})      ; view or reserve of a display the relay lacks

;; ------------------------------------------------------------ rules

(defn- integral
  "x as a long when it is an integer, or a finite float with no fraction; else nil."
  [x]
  (cond
    (int? x) x
    (and (number? x)
         #?(:clj (Double/isFinite (double x)) :cljs (js/isFinite x))
         (== x (Math/floor (double x))))
    (long x)
    :else nil))

(defn- nonblank? [x] (and (string? x) (pos? (count x))))

(defn message-ok?
  "Does a control message to the relay pass its schema (contract/schemas)?
  view: display, if present, a non-empty string. reserve: name a non-empty
  string; display as for view; ttl, if present, an integer >= 1; format, if
  present, pal16, hex or rgb24. renew, release: anything else is ignored."
  [{:keys [op] :as m}]
  (let [opt (fn [k ok?] (or (not (contains? m k)) (ok? (get m k))))]
    (boolean
     (case op
       "view" (opt :display nonblank?)
       "reserve" (and (nonblank? (:name m))
                      (opt :display nonblank?)
                      (opt :ttl #(some-> (integral %) (>= 1)))
                      (opt :format #(contains? source-formats %)))
       ("renew" "release") true
       false))))
(s/fdef message-ok? :args (s/cat :m map?) :ret boolean?)

(defn ttl-ms
  "The ttl rule, in one place: a reserve's ttl (seconds, already past
  `message-ok?`) as ms. Absent -> 300 s; otherwise clamped to 900 s, so 901
  gets 900 (0 and 1.5 never get here: the schema refuses them)."
  [ttl]
  (if (nil? ttl)
    (* 1000 default-ttl-s)
    (* 1000 (min (integral ttl) max-ttl-s))))
(s/fdef ttl-ms
  :args (s/cat :ttl (s/nilable (s/or :int pos-int?
                                     :float (s/with-gen (s/and double? #(>= % 1.0) #(== % (Math/floor %)))
                                              #(gen/fmap double (gen/choose 1 2000))))))
  :ret pos-int?
  :fn #(<= 1000 (:ret %) (* 1000 max-ttl-s)))

(defn expires-s
  "Epoch ms -> the wire's `expires` (unix seconds, rounded up)."
  [ms]
  (quot (+ ms 999) 1000))
(s/fdef expires-s :args (s/cat :ms nat-int?) :ret nat-int?
  :fn #(<= (* 1000 (:ret %)) (+ 999 (-> % :args :ms))))

(defn reordered?
  "The sequence rule, in one place: is a frame with 2-byte sequence `seq`
  dropped (with rate) after the last accepted `last`? A frame with no
  prefix, and the first prefixed frame of a lease, never is; a frame with
  no prefix leaves `last` alone.
    :literal (default) the spec's text, \"a lower sequence than the last
             accepted is dropped\": seq < last. After 65535 every prefixed
             frame is dropped until the lease ends. OPEN QUESTION.
    :serial  RFC 1982 arithmetic over 16 bits: 0 follows 65535."
  ([last seq] (reordered? :literal last seq))
  ([rule last seq]
   (boolean (and last seq
                 (case rule
                   :literal (< seq last)
                   :serial (>= (mod (- seq last) 65536) 32768))))))
(s/fdef reordered? :args (s/cat :rule (s/? #{:literal :serial}) :last (s/nilable ::ds/seq) :seq (s/nilable ::ds/seq))
  :ret boolean?)

(defn too-fast?
  "The fps rule, in one place: GCRA virtual scheduling (ITU-T I.371), as
  the reference relay does it. `tat` is when the next frame is due, kept as
  ms * fps so a period is exactly 1000 and stays an integer (nil before the
  first accepted frame); `tol` is the jitter allowance in thousandths of a
  period (200, i.e. 20%). A frame at `ts` is too fast when
  ts * fps < tat - tol; each accepted frame makes the next due one period
  later (`tat-after`). So a source may jitter but never exceeds fps over
  time: accepted frames i < j satisfy (t_j - t_i) * fps >= (j - i) * 1000 - tol.
  At 60 fps: 0 then 13 ms is too fast, 0 then 14 ms is not; a source at
  exactly 60 fps with whole-ms timestamps (17, 16, 17, ... ms apart) never
  is."
  [tat ts fps tol]
  (boolean (and tat (< (* ts fps) (- tat tol)))))
(s/fdef too-fast? :args (s/cat :tat (s/nilable nat-int?) :ts nat-int? :fps ::ds/fps :tol (s/int-in 0 1000))
  :ret boolean?)

(defn tat-after
  "When the next frame is due (ms * fps) after accepting one at `ts`."
  [tat ts fps]
  (+ (max (* ts fps) (or tat 0)) 1000))
(s/fdef tat-after :args (s/cat :tat (s/nilable nat-int?) :ts nat-int? :fps ::ds/fps) :ret pos-int?
  :fn #(> (:ret %) (* (-> % :args :ts) (-> % :args :fps))))

;; ------------------------------------------------------------ config, state

(defn display-config
  "What the relay needs of a preset: grid, fps, fan-out format, and the
  16-entry palette it announces (level rule applied), also as RGB."
  ([preset] (display-config preset "pal16"))
  ([preset fanout]
   (let [pal (codec/palette16 (caps/palette (:palette preset)))]
     {:w (:w preset) :h (:h preset) :fps (:fps preset) :format fanout
      :palette pal :palette-rgb (mapv codec/hex->rgb pal)})))
(s/fdef display-config
  :args (s/cat :preset (s/keys :req-un [::ds/w ::ds/h ::ds/fps :tetris.displays.preset/palette])
               :fanout (s/? ::ds/format))
  :ret map?)

(s/def ::default string?)
(s/def ::max-viewers pos-int?)
(s/def ::fanout (s/or :every ::ds/format :per-display (s/map-of string? ::ds/format)))
(s/def ::seq-rule #{:literal :serial})
(s/def ::rate-tolerance (s/int-in 0 1000))
(s/def ::name (s/and string? #(pos? (count %))))
(s/def ::extra-display (s/keys :req-un [::name ::ds/w ::ds/h] :opt-un [::ds/fps :tetris.displays.preset/palette]))
(s/def ::extra (s/coll-of ::extra-display))

(defn config
  "The relay's configuration: every preset, plus `extra` mock-only displays
  ({:name :w :h} with optional :fps 30 and :palette \"cga\"); `default`
  (green-building unless given) for a view or reserve that names no
  display; the viewer cap; the fan-out format, \"pal16\" unless given, or a
  map of display -> format where \"*\" names the rest; the sequence rule
  (:literal unless given); the rate tolerance (200 thousandths)."
  ([] (config {}))
  ([{:keys [default max-viewers fanout seq-rule rate-tolerance extra]
     :or {default caps/default-display max-viewers 32 fanout "pal16" seq-rule :literal
          rate-tolerance default-rate-tolerance}}]
   (let [fan (fn [d] (if (map? fanout) (get fanout d (get fanout "*" "pal16")) fanout))
         presets (map (fn [[k p]] [(name k) (display-config p (fan (name k)))]) (:displays caps/caps))
         extras (map (fn [{:keys [name w h fps palette] :or {fps 30 palette "cga"}}]
                       (when (or (caps/preset name) (not (and (int? w) (int? h) (<= 1 w 256) (<= 1 h 256))))
                         (throw (ex-info (str "bad extra display " name " " w "x" h) {:name name})))
                       [name (assoc (display-config {:w w :h h :fps fps :palette palette} (fan name)) :extra? true)])
                     extra)
         displays (into (sorted-map) (concat presets extras))]
     (when-not (contains? displays (name default))
       (throw (ex-info (str "unknown default display: " default) {:default default})))
     {:displays displays
      :default (name default)
      :max-viewers max-viewers
      :seq-rule seq-rule
      :rate-tolerance rate-tolerance})))
(s/fdef config
  :args (s/? (s/keys :opt-un [::max-viewers ::fanout ::seq-rule ::rate-tolerance ::extra]))
  :ret map?)

(defn init
  "The relay with no connections and every display free."
  [config]
  (assoc config :conns (sorted-map) :leases (sorted-map) :granted 0 :watermark nil :anomalies []))
(s/fdef init :args (s/cat :config map?) :ret map?)

;; ------------------------------------------------------------ specs

(s/def ::ts nat-int?)
(s/def ::conn nat-int?)
(s/def ::event #{:open :close :control :frame :expire})
(s/def ::msg map?)
(s/def ::malformed true?)
(s/def ::data (s/or :text string? :octets vector?))
(s/def ::display string?)
(s/def ::lease string?)

(defmulti entry-type :event)
(defmethod entry-type :open [_] (s/keys :req-un [::event ::ts ::conn]))
(defmethod entry-type :close [_] (s/keys :req-un [::event ::ts ::conn]))
(defmethod entry-type :control [_]
  (s/and (s/keys :req-un [::event ::ts ::conn] :opt-un [::msg ::malformed])
         #(or (contains? % :msg) (:malformed %))))
(defmethod entry-type :frame [_] (s/keys :req-un [::event ::ts ::conn ::data]))
(defmethod entry-type :expire [_] (s/keys :req-un [::event ::ts ::display ::lease]))
(defmethod entry-type :default [_] (s/keys :req-un [::event]))

(declare event-gen log-gen step)
(s/def ::log-entry (s/with-gen (s/multi-spec entry-type :event) #(gen/bind (log-gen) gen/elements)))
(s/def ::log (s/with-gen (s/coll-of ::log-entry :kind vector?) #(log-gen)))
(s/def ::any-entry (s/with-gen any? #(s/gen ::log-entry)))
(s/def ::any-log
  (s/with-gen (s/coll-of any? :kind sequential?) #(log-gen)))

(s/def ::fx #{:send :frame :close :arm})
(s/def ::to ::conn)
(s/def ::at nat-int?)
(s/def ::code int?)
(s/def ::reason string?)
(defmulti effect-type :fx)
(defmethod effect-type :send [_] (s/keys :req-un [::fx ::to ::msg]))
(defmethod effect-type :frame [_] (s/keys :req-un [::fx ::to ::data]))
(defmethod effect-type :close [_] (s/keys :req-un [::fx ::to ::code ::reason]))
(defmethod effect-type :arm [_] (s/keys :req-un [::fx ::display ::lease ::at]))
(s/def ::effect (s/multi-spec effect-type :fx))
(s/def ::effects (s/coll-of ::effect :kind vector?))

(s/def ::conns map?)
(s/def ::leases map?)
(s/def ::granted nat-int?)
(s/def ::anomalies vector?)
(s/def ::state
  (s/with-gen (s/keys :req-un [::conns ::leases ::granted ::anomalies])
    #(gen/fmap (fn [log] (reduce (fn [state e] (first (step state e)))
                                 (init (config {:max-viewers 2}))
                                 log))
               (log-gen))))

;; ------------------------------------------------------------ helpers

(defn- send-fx [conn msg] {:fx :send :to conn :msg msg})

(defn- err [conn k] (send-fx conn {:op "error" :reason (reason-for k)}))

(defn viewers
  "The connections viewing display `d`, in connection order."
  [state d]
  (into [] (keep (fn [[c m]] (when (= d (:viewing m)) c))) (:conns state)))
(s/fdef viewers :args (s/cat :state ::state :d string?) :ret (s/coll-of ::conn :kind vector?))

(defn holding
  "The display `conn` holds, or nil."
  [state conn]
  (let [d (get-in state [:conns conn :holding])]
    (when (and d (= conn (get-in state [:leases d :holder]))) d)))
(s/fdef holding :args (s/cat :state ::state :conn ::conn) :ret (s/nilable string?))

(defn- display-of [state msg]
  (let [d (or (:display msg) (:default state))]
    (when (contains? (:displays state) d) d)))

(defn- caps-msg [state d]
  (let [{:keys [w h fps format palette]} (get-in state [:displays d])]
    {:op "caps" :display d :w w :h h :fps fps :format format :palette palette}))

(defn- lease-msg [d l]
  {:op "lease" :display d :holder (:name l) :expires (some-> (:expires l) expires-s)})

(defn- to-viewers [state d msg]
  (mapv #(send-fx % msg) (viewers state d)))

(defn- fanout
  "Cells as display config `dc` fans them out: octets or hex text."
  [dc cells]
  (if (= "hex" (:format dc)) (codec/encode-hex (:w dc) cells) (codec/encode-pal16 cells)))

(defn- end-lease
  "The lease on `d` is over (why: :release or :expire). Viewers get lease
  null; on expiry each then gets a black frame."
  [state d why]
  (let [{:keys [holder]} (get-in state [:leases d])
        dc (get-in state [:displays d])
        state (cond-> (update state :leases dissoc d)
                (contains? (:conns state) holder) (update-in [:conns holder] dissoc :holding))
        black (when (= why :expire) (fanout dc (vec (repeat (* (:w dc) (:h dc)) 0))))]
    [state (into [] (mapcat (fn [v] (cond-> [(send-fx v (lease-msg d nil))]
                                      black (conj {:fx :frame :to v :data black}))))
                 (viewers state d))]))

(defn- renew
  "Lease on `d` renewed at `ts`: expires = ts + ttl; the viewers get a fresh
  lease when the whole-second expires changes."
  [state d ts]
  (let [l (get-in state [:leases d])
        l' (assoc l :expires (+ ts (:ttl-ms l)))
        state (assoc-in state [:leases d] l')]
    [state (if (= (expires-s (:expires l)) (expires-s (:expires l')))
             []
             (to-viewers state d (lease-msg d l')))]))

;; ------------------------------------------------------------ control

(defn- on-view [state {:keys [conn msg]}]
  (let [d (display-of state msg)]
    (cond
      (nil? d) [state [(err conn :unknown-display)]]

      (and (not= d (get-in state [:conns conn :viewing]))
           (>= (count (viewers state d)) (:max-viewers state)))
      [state [{:fx :close :to conn :code viewer-cap-close
               :reason (str "viewer cap " (:max-viewers state))}]]

      :else
      [(assoc-in state [:conns conn :viewing] d)
       [(send-fx conn (caps-msg state d))
        (send-fx conn (lease-msg d (get-in state [:leases d])))]])))

(defn- on-reserve [state {:keys [conn msg ts]}]
  (let [d (display-of state msg)
        l (get-in state [:leases d])]
    (cond
      (nil? d) [state [(err conn :unknown-display)]]

      (and l (not= conn (:holder l)))
      [state [(send-fx conn {:op "busy" :holder (:name l) :expires (expires-s (:expires l))})]]

      :else
      (let [old (holding state conn)
            [state fx0] (if (and old (not= old d)) (end-lease state old :release) [state []])
            id (str "L" (inc (:granted state)))
            fmt (get msg :format "pal16")
            ttl (ttl-ms (:ttl msg))
            exp (+ ts ttl)
            l' {:id id :holder conn :name (:name msg) :format fmt :ttl-ms ttl :expires exp
                :tat nil :last-seq nil}
            state (-> state
                      (assoc-in [:leases d] l')
                      (assoc-in [:conns conn :holding] d)
                      (update :granted inc))
            {:keys [w h fps palette]} (get-in state [:displays d])]
        [state (-> fx0
                   (conj (send-fx conn {:op "granted" :lease id :w w :h h :fps fps :format fmt
                                        :palette palette :expires (expires-s exp)}))
                   (into (to-viewers state d (lease-msg d l')))
                   (conj {:fx :arm :display d :lease id :at exp}))]))))

(defn- on-control [state {:keys [conn msg malformed] :as e}]
  (let [op (:op msg)]
    (cond
      (or malformed (not (map? msg))) [state [(err conn :bad-json)]]
      (not (string? op)) [state [(err conn :no-op)]]
      (not (contains? ops op)) [state [(err conn :unknown-op)]]
      (not (message-ok? msg)) [state [(err conn :bad-message)]]
      (= op "view") (on-view state e)
      (= op "reserve") (on-reserve state e)
      :else
      (if-let [d (holding state conn)]
        (if (= op "renew") (renew state d (:ts e)) (end-lease state d :release))
        [state [(err conn :not-holder)]]))))

;; ------------------------------------------------------------ frames, expiry

(defn decode-frame
  "A holder's frame -> {:cells [...] :seq?} or {:error reason}, for display
  config `dc` and a lease reserved in `fmt`: text is hex; binary is rgb24
  when so reserved and of an rgb24 length, else a BLP or MCUF packet when
  it starts with a magic (its grid must be the display's), else pal16 (or
  bad-frame-length on an rgb24 lease)."
  [dc fmt data]
  (let [{:keys [w h palette-rgb]} dc
        n (* w h)]
    (if (string? data)
      (codec/decode-hex w h data)
      (let [bs (codec/octets data)]
        (cond
          (nil? bs) {:error "bad-format"}
          (and (= "rgb24" fmt) (contains? #{(* 3 n) (+ 2 (* 3 n))} (count bs)))
          (codec/decode-rgb24 w h palette-rgb bs)
          (codec/interop? bs)
          (let [p (codec/parse-interop bs)]
            (cond
              (:error p) p
              (not= [w h] [(:w p) (:h p)]) {:error "bad-frame-length"}
              :else {:cells (codec/interop-cells p palette-rgb)}))
          (= "rgb24" fmt) {:error "bad-frame-length"}
          :else (codec/decode-pal16 w h bs))))))
(s/fdef decode-frame :args (s/cat :dc map? :fmt ::ds/source-format :data any?) :ret ::ds/decoded)

(defn- on-frame [state {:keys [conn data ts]}]
  (if-let [d (holding state conn)]
    (let [dc (get-in state [:displays d])
          l (get-in state [:leases d])
          r (decode-frame dc (:format l) data)]
      (cond
        (:error r) [state [(err conn (keyword (:error r)))]]
        (reordered? (:seq-rule state) (:last-seq l) (:seq r)) [state [(err conn :rate)]]
        (too-fast? (:tat l) ts (:fps dc) (:rate-tolerance state)) [state [(err conn :rate)]]
        :else
        (let [state (update-in state [:leases d] assoc
                               :tat (tat-after (:tat l) ts (:fps dc))
                               :last-seq (if (:seq r) (:seq r) (:last-seq l)))
              [state fx] (renew state d ts)
              out (fanout dc (:cells r))]
          [state (into fx (map (fn [v] {:fx :frame :to v :data out})) (viewers state d))])))
    [state [(err conn :not-holder)]]))

(defn- on-expire [state {:keys [display lease ts]}]
  (let [l (get-in state [:leases display])]
    (cond
      (or (nil? l) (not= lease (:id l))) [state []]
      (< ts (:expires l)) [state [{:fx :arm :display display :lease lease :at (:expires l)}]]
      :else (end-lease state display :expire))))

(defn- on-close [state {:keys [conn]}]
  (let [d (holding state conn)
        state (update state :conns dissoc conn)]
    (if d (end-lease state d :release) [state []])))

;; ------------------------------------------------------------ step, fold, view

(defn- anomaly [state inv event]
  (update state :anomalies conj
          (cond-> {:inv inv :event (:event event)}
            (:ts event) (assoc :ts (:ts event))
            (:conn event) (assoc :conn (:conn event)))))

(defn step
  "One log entry -> [state' effects]."
  [state event]
  (if-not (s/valid? ::log-entry event)
    [(anomaly state "schema" event) []]
    (let [{:keys [ts conn]} event
          wm (:watermark state)
          state (if (and wm (< ts wm))
                  (anomaly state "I6" event)
                  (assoc state :watermark ts))
          open? (contains? (:conns state) conn)]
      (case (:event event)
        :open (if open?
                [(anomaly state "T2" event) []]
                [(assoc-in state [:conns conn] {}) []])
        :close (if open? (on-close state event) [(anomaly state "T2" event) []])
        :control (if open? (on-control state event) [(anomaly state "T2" event) []])
        :frame (if open? (on-frame state event) [(anomaly state "T2" event) []])
        :expire (on-expire state event)))))
(s/fdef step
  :args (s/cat :state ::state :event ::any-entry)
  :ret (s/tuple ::state ::effects)
  :fn (fn [{[state fx] :ret}]
        (and (every? (fn [[d l]] (= d (get-in state [:conns (:holder l) :holding]))) (:leases state))
             (every? (fn [[_ m]] (or (nil? (:holding m)) (contains? (:leases state) (:holding m))))
                     (:conns state))
             (every? (fn [d] (<= (count (viewers state d)) (:max-viewers state))) (keys (:displays state)))
             (every? #(or (not= "error" (get-in % [:msg :op])) (ds/reasons (get-in % [:msg :reason]))) fx))))

(defn fold
  "The log -> {:state s :effects [effects-of-entry-0 ...]}: the relay is a
  deterministic function of its log."
  [config log]
  (reduce (fn [acc e]
            (let [[s fx] (step (:state acc) e)]
              (-> acc (assoc :state s) (update :effects conj fx))))
          {:state (init config) :effects []}
          log))
(s/fdef fold :args (s/cat :config map? :log ::any-log) :ret (s/keys :req-un [::state ::effects]))

(defn- arms->timers [fx n]
  (into [] (comp (filter #(= :arm (:fx %)))
                 (map-indexed (fn [i a] [(:at a) (+ n i) (:display a) (:lease a)])))
        fx))

(defn run
  "Play external `events` (ascending :ts) through the fold with a simulated
  scheduler: each :arm effect has it write {:event :expire} into the log at
  :at, before the first external event at or after that time (the bb
  relay's timer does the same in real time). Timers still pending after the
  last event are written at `until` if it is given. Returns {:log :state
  :effects}; (fold config log) reproduces :state and :effects."
  ([config events] (run config events nil))
  ([config events until]
   (loop [state (init config) timers (sorted-set) n 0 events (seq events) log [] effects []]
     (let [t (first timers)
           e (first events)]
       (cond
         (and t (or (and e (<= (first t) (:ts e))) (and (nil? e) until (<= (first t) until))))
         (let [[at _ d id] t
               ev {:event :expire :ts at :display d :lease id}
               [s fx] (step state ev)
               ts (arms->timers fx n)]
           (recur s (into (disj timers t) ts) (+ n (count ts)) events (conj log ev) (conj effects fx)))

         e
         (let [[s fx] (step state e)
               ts (arms->timers fx n)]
           (recur s (into timers ts) (+ n (count ts)) (next events) (conj log e) (conj effects fx)))

         :else {:log log :state state :effects effects})))))
(s/fdef run
  :args (s/cat :config map? :events ::log :until (s/? (s/nilable nat-int?)))
  :ret (s/keys :req-un [::log ::state ::effects]))

(s/def ::displays map?)

(defn view
  "The projection: per display :free, or :held with holder and expires (unix
  s), and the viewer count; the anomalies, plus X1 for each lease whose
  deadline is behind the watermark with no expiry event written yet."
  [state]
  (let [wm (:watermark state)]
    {:displays (into (sorted-map)
                     (map (fn [d]
                            (let [l (get-in state [:leases d])]
                              [d (cond-> {:state (if l :held :free) :viewers (count (viewers state d))}
                                   l (assoc :holder (:name l) :expires (expires-s (:expires l))))])))
                     (keys (:displays state)))
     :anomalies (into (:anomalies state)
                      (for [[d l] (:leases state)
                            :when (and wm (< (:expires l) wm))]
                        {:inv "X1" :display d :lease (:id l) :due (:expires l) :watermark wm}))}))
(s/fdef view :args (s/cat :state ::state) :ret (s/keys :req-un [::displays ::anomalies]))

;; ------------------------------------------------------------ generators

(def test-displays
  "The displays the generators aim at: this repo's default and a small one."
  ["green-building" "trs80"])

(defn- frame-data-gen []
  (gen/one-of
   [(gen/fmap (fn [[d fmt seq cells]]
                (let [{:keys [w h]} (caps/preset d)
                      cells (vec (take (* w h) (cycle cells)))]
                  (case fmt
                    :pal16 cells
                    :seq (codec/encode-pal16 cells seq)
                    :hex (codec/encode-hex w cells)
                    :rgb24 (vec (mapcat (fn [c] [(* 17 c) 0 0]) cells))
                    :blp (codec/encode-blp w h (map #(if (pos? %) 1 0) cells))
                    :short (pop cells)
                    :byte16 (assoc cells 0 16))))
              (gen/tuple (gen/elements test-displays)
                         (gen/elements [:pal16 :pal16 :seq :seq :seq :hex :rgb24 :blp :short :byte16])
                         (gen/elements [0 1 2 65534 65535])
                         (gen/vector (gen/choose 0 15) 1 4)))
    (gen/return "g")]))

(defn event-gen
  "External relay events without :ts: connections 1..4, displays
  green-building and trs80 (and an unknown one), ttl at 0, 1, 900, 901 and
  1.5, frames valid and broken, sequences at 0 and 65535."
  []
  (let [conn (gen/choose 1 4)
        disp (gen/elements (conj test-displays nil "nope"))]
    (gen/frequency
     [[1 (gen/fmap (fn [c] {:event :open :conn c}) conn)]
      [1 (gen/fmap (fn [c] {:event :close :conn c}) conn)]
      [3 (gen/fmap (fn [[c d]] {:event :control :conn c :msg (cond-> {:op "view"} d (assoc :display d))})
                   (gen/tuple conn disp))]
      [3 (gen/fmap (fn [[c d ttl fmt nm]]
                     {:event :control :conn c
                      :msg (cond-> {:op "reserve"}
                             nm (assoc :name (str nm c))
                             d (assoc :display d) ttl (assoc :ttl ttl) fmt (assoc :format fmt))})
                   (gen/tuple conn disp (gen/elements [nil 0 1 900 901 1.5 "x"])
                              (gen/elements [nil "pal16" "hex" "rgb24" "idx4"])
                              (gen/elements ["src" "src" "src" nil])))]
      [1 (gen/fmap (fn [[c op]] {:event :control :conn c :msg (cond-> {} op (assoc :op op))})
                   (gen/tuple conn (gen/elements ["renew" "release" "frobnicate" nil 7])))]
      [1 (gen/fmap (fn [c] {:event :control :conn c :malformed true}) conn)]
      [8 (gen/fmap (fn [[c data]] {:event :frame :conn c :data data}) (gen/tuple conn (frame-data-gen)))]])))

(defn log-gen
  "Logs of external events: connections 1..4 open at ts 0, then events at
  gaps that straddle the fps and ttl boundaries."
  []
  (gen/fmap (fn [pairs]
              (let [opens (mapv (fn [c] {:event :open :conn c :ts 0}) (range 1 5))]
                (second (reduce (fn [[t acc] [dt e]] (let [t (+ t dt)] [t (conj acc (assoc e :ts t))]))
                                [0 opens] pairs))))
            (gen/vector (gen/tuple (gen/elements [0 1 13 14 16 17 26 27 33 34 1000 899999 900000 900001])
                                   (event-gen))
                        0 40)))
