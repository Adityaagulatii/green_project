(ns tetris.displays.lease
  "The wal.sh/tools/display relay as a pure fold (inputs/wal-sh-display-protocol.md):

    (step state event) -> [state' effects]

  Events come from the relay's I/O shell: a socket opened, a text (JSON
  control) or binary (frame) message, a socket closed, and the relay's own
  timer firing. Time enters only through events (:now, epoch ms), so the same
  event log always gives the same states and effects (the state-machine
  ladder's rule that the relay's timer writes the expiry: an :arm effect asks
  the shell for an :expire event, and only that event ends a lease).

  Effects:
    {:fx :send    :to conn :msg map}      a JSON text message
    {:fx :forward :to conn :skip k}       the current binary message minus a
                                          k-byte sequence prefix
    {:fx :black   :to conn :len n}        an all-black frame of n bytes
    {:fx :arm     :display d :lease id :at ms}
                                          deliver {:ev :expire ...} at ms

  Decisions where the protocol text is silent (documented in the README):
  a reserve by the holder renews and re-grants the same lease; a connection
  holds at most one lease (reserving another display ends the first); ttl is
  clamped to (0, 900] s, default 300; release and close end the lease like an
  expiry (viewers get holder null and a black frame); renewals by frames are
  not broadcast, an explicit renew is; `expires` is epoch seconds, rounded up;
  frames closer than floor(1000/fps) ms to the last accepted frame are
  dropped with \"rate\". Extra error reasons: \"bad message\" (not JSON, no
  known op) and \"unknown display\"."
  (:require [clojure.spec.alpha :as s]
            [clojure.spec.gen.alpha :as gen]))

(def max-ttl-s 900)
(def default-ttl-s 300)
(def error-reasons #{"not holder" "bad frame length" "rate" "bad message" "unknown display"})

;; ------------------------------------------------------------ specs

(s/def ::w (s/int-in 1 257))
(s/def ::h (s/int-in 1 257))
(s/def ::fps (s/int-in 1 61))
(s/def ::caps (s/keys :req-un [::w ::h ::fps]))
(s/def ::display (s/with-gen (s/and string? seq) #(gen/elements ["a" "b" "nope"])))
(s/def ::displays (s/map-of ::display ::caps :min-count 1))
(s/def ::default ::display)
(s/def ::config (s/and (s/keys :req-un [::displays ::default])
                       #(contains? (:displays %) (:default %))))

(s/def ::conn (s/with-gen nat-int? #(gen/choose 0 5)))
(s/def ::now (s/int-in 0 4102444800000))
(s/def ::viewing ::display)
(s/def ::holding ::display)
(s/def ::conn-state (s/keys :opt-un [::viewing ::holding]))
(s/def ::conns (s/map-of ::conn ::conn-state))
(s/def ::id string?)
(s/def ::holder ::conn)
(s/def ::name string?)
(s/def ::ttl-ms pos-int?)
(s/def ::expires ::now)
(s/def ::last-frame (s/nilable ::now))
(s/def ::lease-state (s/keys :req-un [::id ::holder ::name ::ttl-ms ::expires]
                             :opt-un [::last-frame]))
(s/def ::leases (s/map-of ::display ::lease-state))
(s/def ::seq nat-int?)

(declare init)
(defn- config-gen []
  (gen/return {:displays {"a" {:w 9 :h 17 :fps 30} "b" {:w 2 :h 2 :fps 8}}
               :default "a"}))

(s/def ::state
  (s/with-gen (s/keys :req-un [::displays ::default ::conns ::leases ::seq])
    #(gen/fmap init (config-gen))))

(s/def ::op string?)
(s/def ::ttl number?)
(defn- msg-gen []
  (gen/one-of
   [(gen/fmap (fn [d] {:op "view" :display d}) (s/gen ::display))
    (gen/return {:op "view"})
    (gen/fmap (fn [[d t]] {:op "reserve" :name "src" :display d :ttl t})
              (gen/tuple (s/gen ::display) (gen/elements [1 5 300 900 1000 0 -1 0.5])))
    (gen/return {:op "renew"})
    (gen/return {:op "release"})
    (gen/return {:op "frobnicate"})
    (gen/return nil)]))
(s/def ::msg (s/with-gen (s/nilable map?) msg-gen))
(s/def ::len nat-int?)
(s/def ::lease string?)
(s/def ::ev #{:open :close :text :binary :expire})

(defmulti event-type :ev)
(defmethod event-type :open [_] (s/keys :req-un [::ev ::conn ::now]))
(defmethod event-type :close [_] (s/keys :req-un [::ev ::conn ::now]))
(defmethod event-type :text [_] (s/keys :req-un [::ev ::conn ::now ::msg]))
(defmethod event-type :binary [_] (s/keys :req-un [::ev ::conn ::now ::len]))
(defmethod event-type :expire [_] (s/keys :req-un [::ev ::display ::lease ::now]))
(s/def ::event (s/multi-spec event-type :ev))

(s/def ::fx #{:send :forward :black :arm})
(s/def ::to ::conn)
(s/def ::skip #{0 2})
(s/def ::at ::now)
(defmulti effect-type :fx)
(defmethod effect-type :send [_] (s/keys :req-un [::fx ::to ::msg]))
(defmethod effect-type :forward [_] (s/keys :req-un [::fx ::to ::skip]))
(defmethod effect-type :black [_] (s/keys :req-un [::fx ::to ::len]))
(defmethod effect-type :arm [_] (s/keys :req-un [::fx ::display ::lease ::at]))
(s/def ::effect (s/multi-spec effect-type :fx))
(s/def ::effects (s/coll-of ::effect :kind vector?))

;; ------------------------------------------------------------ state

(defn init
  "The relay with no connections. `displays` maps a display name (a d=
  profile) to its caps {:w :h :fps}."
  [{:keys [displays default]}]
  {:displays displays :default default :conns {} :leases {} :seq 0})
(s/fdef init :args (s/cat :config ::config) :ret ::state)

(defn expires-s
  "Epoch ms -> the protocol's `expires` (epoch seconds, rounded up)."
  [ms]
  (quot (+ ms 999) 1000))
(s/fdef expires-s :args (s/cat :ms ::now) :ret nat-int?
        :fn #(<= (-> % :args :ms) (* 1000 (:ret %))))

(defn min-interval-ms
  "Frames closer together than this are over `fps`."
  [fps]
  (quot 1000 fps))
(s/fdef min-interval-ms :args (s/cat :fps ::fps) :ret nat-int?)

(defn frame-len [{:keys [w h]}] (* w h 3))
(s/fdef frame-len :args (s/cat :caps ::caps) :ret pos-int?)

(defn ttl-ms
  "A reserve's ttl (seconds) as ms: clamped to (0, 900] s, 300 s if absent."
  [ttl]
  (if (and (number? ttl) (pos? ttl))
    (max 1 (long (+ 0.5 (* 1000 (min ttl max-ttl-s)))))
    (* 1000 default-ttl-s)))
(s/fdef ttl-ms :args (s/cat :ttl any?) :ret pos-int?
        :fn #(<= (:ret %) (* 1000 max-ttl-s)))

;; ------------------------------------------------------------ helpers

(defn- send-fx [conn msg] {:fx :send :to conn :msg msg})
(defn- err [conn reason] (send-fx conn {:op "error" :reason reason}))

(defn viewers
  "Connections viewing display `d`, in conn order."
  [state d]
  (sort (keep (fn [[c m]] (when (= d (:viewing m)) c)) (:conns state))))
(s/fdef viewers :args (s/cat :state ::state :d ::display) :ret (s/coll-of ::conn))

(defn holding
  "The display `conn` holds, if any."
  [state conn]
  (let [d (get-in state [:conns conn :holding])]
    (when (and d (= conn (get-in state [:leases d :holder]))) d)))
(s/fdef holding :args (s/cat :state ::state :conn ::conn) :ret (s/nilable ::display))

(defn- lease-msg [d lease]
  {:op "lease" :display d :holder (:name lease)
   :expires (some-> (:expires lease) expires-s)})

(defn- to-viewers [state d msg]
  (mapv #(send-fx % msg) (viewers state d)))

(defn- end-lease
  "Lease on `d` over: every viewer gets holder null and a black frame."
  [state d]
  (let [{:keys [holder]} (get-in state [:leases d])
        caps (get-in state [:displays d])
        state (cond-> (update state :leases dissoc d)
                (contains? (:conns state) holder) (update-in [:conns holder] dissoc :holding))]
    [state (into [] (mapcat (fn [v] [(send-fx v (lease-msg d nil))
                                     {:fx :black :to v :len (frame-len caps)}]))
                 (viewers state d))]))

;; ------------------------------------------------------------ messages

(defn- on-view [state {:keys [conn msg]}]
  (let [d (or (:display msg) (:default state))]
    (if-let [{:keys [w h fps]} (get-in state [:displays d])]
      [(assoc-in state [:conns conn :viewing] d)
       [(send-fx conn {:op "caps" :display d :w w :h h :fps fps})
        (send-fx conn (lease-msg d (get-in state [:leases d])))]]
      [state [(err conn "unknown display")]])))

(defn- on-reserve [state {:keys [conn msg now]}]
  (let [d (or (:display msg) (:default state))
        caps (get-in state [:displays d])
        lease (get-in state [:leases d])
        nm (let [n (:name msg)] (if (and (string? n) (seq n)) n "anonymous"))]
    (cond
      (nil? caps) [state [(err conn "unknown display")]]

      (and lease (not= conn (:holder lease)))
      [state [(send-fx conn {:op "busy" :holder (:name lease) :expires (expires-s (:expires lease))})]]

      :else
      (let [old (holding state conn)
            [state fx0] (if (and old (not= old d)) (end-lease state old) [state []])
            id (if lease (:id lease) (str "L" (inc (:seq state))))
            ttl (ttl-ms (:ttl msg))
            exp (+ now ttl)
            lease' {:id id :holder conn :name nm :ttl-ms ttl :expires exp
                    :last-frame (:last-frame lease)}
            state (cond-> (-> state
                              (assoc-in [:leases d] lease')
                              (assoc-in [:conns conn :holding] d))
                    (nil? lease) (update :seq inc))]
        [state (cond-> (-> fx0
                           (conj (send-fx conn {:op "granted" :lease id :w (:w caps) :h (:h caps)
                                                :fps (:fps caps) :expires (expires-s exp)}))
                           (into (to-viewers state d (lease-msg d lease'))))
                 (nil? lease) (conj {:fx :arm :display d :lease id :at exp}))]))))

(defn- on-renew [state {:keys [conn now]}]
  (if-let [d (holding state conn)]
    (let [state (update-in state [:leases d] #(assoc % :expires (+ now (:ttl-ms %))))]
      [state (to-viewers state d (lease-msg d (get-in state [:leases d])))])
    [state [(err conn "not holder")]]))

(defn- on-release [state {:keys [conn]}]
  (if-let [d (holding state conn)]
    (end-lease state d)
    [state [(err conn "not holder")]]))

(defn- on-text [state {:keys [conn msg] :as e}]
  (case (when (map? msg) (:op msg))
    "view"    (on-view state e)
    "reserve" (on-reserve state e)
    "renew"   (on-renew state e)
    "release" (on-release state e)
    [state [(err conn "bad message")]]))

(defn- on-binary [state {:keys [conn now len]}]
  (if-let [d (holding state conn)]
    (let [caps (get-in state [:displays d])
          n (frame-len caps)
          {:keys [last-frame ttl-ms]} (get-in state [:leases d])]
      (cond
        (not (or (= len n) (= len (+ n 2))))
        [state [(err conn "bad frame length")]]

        (and last-frame (< (- now last-frame) (min-interval-ms (:fps caps))))
        [state [(err conn "rate")]]

        :else
        [(update-in state [:leases d] assoc :last-frame now :expires (+ now ttl-ms))
         (mapv (fn [v] {:fx :forward :to v :skip (- len n)}) (viewers state d))]))
    [state [(err conn "not holder")]]))

(defn- on-expire [state {:keys [display lease now]}]
  (let [l (get-in state [:leases display])]
    (cond
      (or (nil? l) (not= lease (:id l))) [state []]
      (< now (:expires l)) [state [{:fx :arm :display display :lease lease :at (:expires l)}]]
      :else
      (let [[state fx] (end-lease state display)
            holder (:holder l)]
        [state (cond-> fx
                 (and (contains? (:conns state) holder)
                      (not-any? #(= holder (:to %)) fx))
                 (conj (send-fx holder (lease-msg display nil))))]))))

(defn- on-close [state {:keys [conn]}]
  (let [d (holding state conn)
        state (update state :conns dissoc conn)]
    (if d (end-lease state d) [state []])))

(defn step
  "One relay event -> [state' effects]."
  [state event]
  (case (:ev event)
    :open   [(update-in state [:conns (:conn event)] #(or % {})) []]
    :close  (on-close state event)
    :text   (on-text state event)
    :binary (on-binary state event)
    :expire (on-expire state event)))
(s/fdef step
  :args (s/cat :state ::state :event ::event)
  :ret (s/tuple ::state ::effects)
  :fn (fn [{:keys [ret]}]
        (let [[state fx] ret]
          (and (every? (fn [[d l]] (= d (get-in state [:conns (:holder l) :holding])))
                       (:leases state))
               (every? #(or (not= "error" (get-in % [:msg :op]))
                            (error-reasons (get-in % [:msg :reason])))
                       fx)))))

(defn simulate
  "Run external `events` (ascending :now) through the fold, with the relay's
  timer simulated: each :arm effect schedules an :expire event, delivered
  before the first external event at or after its time. Timers still pending
  after the last event fire at `until` (ms) if given. Returns
  {:state s :log [[event effects] ...]}."
  ([state events] (simulate state events nil))
  ([state events until]
   (loop [state state
          timers (sorted-set)          ; [at n display lease]
          n 0
          events (seq events)
          log []]
     (let [t (first timers)
           e (first events)]
       (cond
         (and t (or (and e (<= (first t) (:now e)))
                    (and (nil? e) until (<= (first t) until))))
         (let [[at _ d id] t
               ev {:ev :expire :display d :lease id :now at}
               [s fx] (step state ev)
               arms (filter #(= :arm (:fx %)) fx)]
           (recur s (into (disj timers t) (map-indexed (fn [i a] [(:at a) (+ n i) (:display a) (:lease a)])) arms)
                  (+ n (count arms)) events (conj log [ev fx])))

         e
         (let [[s fx] (step state e)
               arms (filter #(= :arm (:fx %)) fx)]
           (recur s (into timers (map-indexed (fn [i a] [(:at a) (+ n i) (:display a) (:lease a)])) arms)
                  (+ n (count arms)) (next events) (conj log [e fx])))

         :else {:state state :log log})))))
(s/fdef simulate
  :args (s/cat :state ::state :events (s/coll-of ::event) :until (s/? (s/nilable ::now)))
  :ret (s/keys :req-un [::state]))
