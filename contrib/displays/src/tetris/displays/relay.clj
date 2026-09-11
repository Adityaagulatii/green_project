(ns tetris.displays.relay
  "`bb relay --port N`: the wal.sh/tools/display v0.2.1 relay on http-kit's
  WebSocket server, driven by the pure fold in tetris.displays.lease.
  Loopback only; nothing here talks to wal.sh.

  The shell is all the I/O there is. It turns socket callbacks into log
  events stamped with a non-decreasing event time, folds each through
  lease/step under one lock (so arrival order is event-time order, ladder
  I6), and performs the effects: JSON text, frames, closes, and :arm, which
  starts a timer that writes the :expire event into the log; only the fold
  decides what an expiry means. --log FILE appends the log as EDN lines,
  and (lease/fold config log) replays it. --record FILE appends every
  message in and out as JSONL, the shape contract/check_session.py reads.

  Transport: WebSocket upgrades are accepted on any path (the advertised
  one is /tools/display/ws); ?view=NAME on the URL views on connect; GET
  /tools/display/capabilities.json (or /capabilities.json) serves the pinned
  advertisement with this relay's default, endpoints and fan-out format.
  A viewer over the cap is closed with 1013 on the JVM; babashka's http-kit
  does not allow a close code, so under bb that close is 1000."
  (:require [babashka.cli :as cli]
            [cheshire.core :as json]
            [clojure.java.io :as io]
            [clojure.spec.alpha :as s]
            [clojure.string :as str]
            [org.httpkit.server :as http]
            [tetris.displays.caps :as caps]
            [tetris.displays.codec :as codec]
            [tetris.displays.lease :as lease])
  (:import [java.net URLDecoder]))

(def ws-path "/tools/display/ws")
(def caps-paths #{"/tools/display/capabilities.json" "/capabilities.json"})
(def loopback #{"127.0.0.1" "::1" "localhost"})

(defn text->event
  "A WebSocket text message as a log event (without :conn and :ts): control
  when it starts with `{` (JSON, keys keywordized; :malformed when it is
  not a JSON object), else a hex frame."
  [text]
  (if (str/starts-with? text "{")
    (let [m (try (json/parse-string text true) (catch Exception _ nil))]
      (if (map? m) {:event :control :msg m} {:event :control :malformed true}))
    {:event :frame :data text}))
(s/fdef text->event :args (s/cat :text string?) :ret map?)

(defn advertisement
  "The pinned capabilities.json as this relay advertises itself: its
  default, local endpoints, each display's fan-out format; every other
  field verbatim."
  [cfg url]
  (let [base (-> url (str/replace-first "ws://" "http://") (str/replace #"/[^/]*$" ""))]
    (-> (json/parse-string (slurp (io/resource caps/json-resource)))
        (assoc "default" (:default cfg))
        (assoc "endpoints" {"ws" url
                            "view" (str url " then {\"op\":\"view\",\"display\":...}")
                            "reserve" (str url " then {\"op\":\"reserve\",\"name\":...,\"display\":...,\"ttl\":...}")
                            "page" (str base "/?d=<display>")})
        (update "displays"
                (fn [ds]
                  (into {} (map (fn [[k {:keys [w h fps format]}]]
                                  [k (-> (or (get ds k)
                                             {"w" w "h" h "palette" "cga" "kind" "mock" "levels" 16 "mono" false
                                              "note" "mock-only display of the bb relay"})
                                         (assoc "fps" fps "format" format))]))
                        (:displays cfg))))
        (assoc-in ["interop" "udp_port"] nil)
        (assoc "status" {"relay" "local bb relay (contrib/displays tetris.displays.relay) on loopback; not wal.sh; no UDP"}))))
(s/fdef advertisement :args (s/cat :cfg map? :url string?) :ret map?)

(defn- query-view [qs]
  (when-let [[_ v] (and qs (re-find #"(?:^|&)view=([^&]*)" qs))]
    (URLDecoder/decode ^String v "UTF-8")))

(defn- daemon [f]
  (doto (Thread. ^Runnable f) (.setDaemon true) (.start)))

(defn- close-with
  "Close with `code` (reflective AsyncChannel.serverClose on the JVM); bb's
  http-kit does not allow it, so there the close is http/close (1000)."
  [ch code]
  (try (.serverClose ch (int code))
       (catch Throwable _ (http/close ch))))

(defn- hex-bytes [^bytes bs] (apply str (map #(format "%02x" (bit-and % 0xff)) bs)))

(defn start!
  "Start a relay on loopback. Options: :host (127.0.0.1), :port (8765; 0
  picks a free one), :default (green-building), :max-viewers (32), :fanout
  (pal16), :seq-rule (:literal), :log (EDN event log file), :record (JSONL
  session file). Returns {:port :url :default :stop :state :view}."
  [{:keys [host port default max-viewers fanout seq-rule log record extra]
    :or {host "127.0.0.1" port 8765 default caps/default-display max-viewers lease/max-viewers
         fanout "pal16" seq-rule :literal}}]
  (when-not (loopback host)
    (throw (ex-info (str "the relay binds to loopback only, not " host) {:host host})))
  (let [cfg (lease/config {:default default :max-viewers max-viewers :fanout fanout
                           :seq-rule (keyword seq-rule) :extra extra})
        lk (Object.)
        st (atom (lease/init cfg))
        chans (atom {})
        ids (atom 0)
        clock (atom 0)
        logw (when log (io/writer (io/file log) :append true))
        recw (when record (io/writer (io/file record) :append true))
        url (promise)]
    (letfn [(rec! [conn dir kind payload]
              (when recw
                (.write ^java.io.Writer recw
                        (str (json/generate-string {:t (/ (System/currentTimeMillis) 1000.0)
                                                    :conn (str "c" conn) :dir dir :kind kind
                                                    :payload payload})
                             "\n"))
                (.flush ^java.io.Writer recw)))
            (submit! [ev & [arrival]]
              (locking lk
                (let [ts (swap! clock #(max % (or arrival (System/currentTimeMillis))))
                      ev (assoc ev :ts ts)
                      [s fx] (lease/step @st ev)]
                  (reset! st s)
                  (when logw (.write ^java.io.Writer logw (str (pr-str ev) "\n")) (.flush ^java.io.Writer logw))
                  (run! perform! fx))))
            (perform! [{:keys [fx to] :as f}]
              (let [ch (@chans to)]
                (case fx
                  :send (when ch
                          (let [text (json/generate-string (:msg f))]
                            (rec! to "out" "text" text)
                            (http/send! ch text)))
                  :frame (when ch
                           (let [d (:data f)]
                             (if (string? d)
                               (do (rec! to "out" "text" d) (http/send! ch d))
                               (let [bs (byte-array d)]
                                 (rec! to "out" "binary" (hex-bytes bs))
                                 (http/send! ch bs)))))
                  :close (when ch
                           (rec! to "out" "close" {:code (:code f)})
                           (close-with ch (:code f)))
                  :arm (let [{:keys [display lease at]} f
                             delay (max 0 (- at @clock))]
                         (daemon (fn []
                                   (Thread/sleep (long delay))
                                   (submit! {:event :expire :display display :lease lease})))))))
            (handler [req]
              (cond
                (:websocket? req)
                (let [conn (swap! ids inc)
                      view (query-view (:query-string req))]
                  (http/as-channel
                   req
                   {:on-open (fn [ch]
                               (swap! chans assoc conn ch)
                               (locking lk (rec! conn "in" "open" {:path (str (:uri req)
                                                                              (some->> (:query-string req) (str "?")))}))
                               (submit! {:event :open :conn conn})
                               (when view
                                 (submit! {:event :control :conn conn :msg {:op "view" :display view}})))
                    :on-receive (fn [_ m]
                                  ;; event time is arrival time, taken before the lock
                                  (let [arrival (System/currentTimeMillis)
                                        ev (assoc (if (string? m)
                                                    (text->event m)
                                                    {:event :frame :data (codec/octets m)})
                                                  :conn conn)]
                                    (locking lk
                                      (when recw
                                        (if (string? m)
                                          (rec! conn "in" "text" m)
                                          (rec! conn "in" "binary" (hex-bytes m))))
                                      (submit! ev arrival))))
                    :on-close (fn [_ status]
                                (locking lk
                                  (rec! conn "in" "close" {:code status})
                                  (submit! {:event :close :conn conn}))
                                (swap! chans dissoc conn))}))

                (caps-paths (:uri req))
                {:status 200 :headers {"Content-Type" "application/json"}
                 :body (json/generate-string (advertisement cfg @url) {:pretty true})}

                :else {:status 404 :headers {"Content-Type" "text/plain"} :body "not found\n"}))]
      (let [srv (http/run-server handler {:ip host :port port :legacy-return-value? false
                                          :max-ws (* 1 1024 1024)})
            p (http/server-port srv)
            u (str "ws://" host ":" p ws-path)]
        (deliver url u)
        {:port p :url u :default (:default cfg)
         :stop (fn []
                 (http/server-stop! srv)
                 (when logw (.close ^java.io.Writer logw))
                 (when recw (.close ^java.io.Writer recw)))
         :state (fn [] @st)
         :view (fn [] (lease/view @st))}))))
(s/fdef start! :args (s/cat :opts map?) :ret map?)

(def cli-spec
  {:port {:desc "TCP port on loopback; 0 picks a free one" :coerce :long :default 8765}
   :host {:desc "loopback address to bind" :default "127.0.0.1"}
   :default {:desc "display an omitted `display` resolves to (and the advertised default)"
             :default caps/default-display}
   :max-viewers {:desc "viewers per display" :coerce :long :default lease/max-viewers}
   :fanout {:desc "fan-out: pal16 or hex for every display, or NAME=FORMAT (repeatable)" :coerce []}
   :extra-display {:desc "a mock-only display NAME=WxH[,fanout=hex][,palette=cga][,fps=30] (repeatable)"
                   :coerce []}
   :seq-rule {:desc "sequence rule: literal (the spec's text) or serial (RFC 1982)" :default "literal"}
   :log {:desc "append the event log (EDN lines) to this file"}
   :record {:desc "append every message in and out (JSONL, check_session.py) to this file"}
   :help {:desc "this help" :coerce :boolean}})

(defn parse-extra
  "NAME=WxH[,fanout=hex][,palette=cga][,fps=30] -> {:name :w :h :fps :palette :fanout}."
  [text]
  (let [[nm rest] (str/split text #"=" 2)
        [geometry & opts] (str/split (or rest "") #",")
        [w h] (map parse-long (str/split geometry #"x"))]
    (reduce (fn [m o]
              (let [[k v] (str/split o #"=" 2)]
                (case k
                  "fanout" (assoc m :fanout v)
                  "palette" (assoc m :palette v)
                  "fps" (assoc m :fps (parse-long v))
                  (throw (ex-info (str "--extra-display option " k) {:option k})))))
            {:name nm :w w :h h :fps 30 :palette "cga"}
            opts)))
(s/fdef parse-extra :args (s/cat :text string?) :ret map?)

(defn parse-fanout
  "--fanout values -> \"pal16\" / \"hex\", or {display format, \"*\" format}."
  [vs extras]
  (let [pairs (concat (for [v vs] (if (str/includes? v "=") (str/split v #"=" 2) ["*" v]))
                      (for [{:keys [name fanout]} extras :when fanout] [name fanout]))]
    (if (every? #(= "*" (first %)) pairs)
      (or (second (last pairs)) "pal16")
      (into {} pairs))))
(s/fdef parse-fanout :args (s/cat :vs (s/nilable (s/coll-of string?)) :extras (s/nilable (s/coll-of map?)))
  :ret (s/or :every string? :per-display map?))

(defn -main
  "bb relay [--port 8765] [--default green-building] [--fanout pal16|NAME=hex] [--extra-display N=WxH] [--log F] [--record F]"
  [& args]
  (let [opts (cli/parse-opts args {:spec cli-spec})
        extras (mapv parse-extra (:extra-display opts))
        opts (assoc opts :extra extras :fanout (parse-fanout (:fanout opts) extras))]
    (if (:help opts)
      (println (str "bb relay [options]\n" (cli/format-opts {:spec cli-spec})))
      (let [{:keys [url default]} (start! opts)]
        (println (str "relay listening " url " default=" default " spec=" caps/spec-version))
        (flush)
        @(promise)))))
