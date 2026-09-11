;;; tetris-mit.el --- 17x9 Tetris for MIT's Green Building, from Emacs  -*- lexical-binding: t; -*-

;; Copyright (C) 2026 17x9-Tetris contributors

;; Version: 0.1.0
;; Package-Requires: ((emacs "28.1"))
;; Keywords: games
;; URL: https://github.com/aygp-dr/17x9-Tetris

;; This file is not part of GNU Emacs.

;;; Commentary:

;; The 17x9 Tetris of the MIT Green Building facade is 153 windows,
;; 17 rows by 9 columns, at 30 FPS.  This package plays it from Emacs
;; in three ways:
;;
;; `tetris-mit-remote'  A thin client for the SPEC v1 engine server,
;;                      `python -m tetris_sim.server --mode engine'.
;;                      Keys become protocol events, and the frames that
;;                      come back are drawn with face backgrounds.  It is
;;                      SPEC-faithful, because the engine decides
;;                      everything.
;;
;; `tetris-mit-kav'     The test driver.  It replays the known-answer
;;                      vectors (KAV-NN = spec/conformance/traces/NN-*.json)
;;                      through a lockstep engine server and checks every
;;                      frame digest.  `tetris-mit-kav-batch' is the CI
;;                      entry point.
;;
;; `tetris-mit-local'   Emacs's own `tetris' (tetris.el) on a 9-wide,
;;                      17-tall board in the SPEC palette.  It can mirror
;;                      its board to a display server.  The rules are
;;                      tetris.el's, not SPEC v1.
;;
;; The wire protocol is docs/PROTOCOL.md, contract v1 (protocol version
;; 1): newline-delimited JSON over TCP, on loopback by default.  A hello
;; offers version 1 and falls back to 0 when a draft-v0 server refuses
;; it.  The client acts on the server hello's `client_role' (a gatekeeper
;; may demote a controller), and the KAV driver compares each engine
;; frame's `events' with the trace.  See contrib/emacs/README.md.

;;; Code:

(require 'cl-lib)
(require 'json)
(require 'subr-x)
(require 'gamegrid)
(require 'tetris)

;;;; Options and constants

(defgroup tetris-mit nil
  "17x9 Tetris for the MIT Green Building facade."
  :group 'games
  :prefix "tetris-mit-")

(defconst tetris-mit--directory
  (file-name-directory (or load-file-name buffer-file-name default-directory))
  "The directory tetris-mit.el was loaded from.")

(defcustom tetris-mit-root (expand-file-name "../../" tetris-mit--directory)
  "Root of the 17x9-Tetris checkout.
The Python server and the conformance traces are found under it."
  :type 'directory)

(defcustom tetris-mit-python (or (getenv "TETRIS_MIT_PYTHON") "python3")
  "Python used to start a private server, for example by `tetris-mit-kav'."
  :type 'string)

(defcustom tetris-mit-host "127.0.0.1"
  "Host of the engine server that `tetris-mit-remote' connects to.
Protocol v0 has no authentication.  Keep servers on loopback, and use
a tunnel such as \"ssh -L\" to reach one on another machine."
  :type 'string)

(defcustom tetris-mit-port 1709
  "Port of the engine server that `tetris-mit-remote' connects to."
  :type 'natnum)

(defcustom tetris-mit-display-host "127.0.0.1"
  "Host of the display server that `tetris-mit-local' mirrors to."
  :type 'string)

(defcustom tetris-mit-display-port 1709
  "Port of the display server that `tetris-mit-local' mirrors to."
  :type 'natnum)

(defcustom tetris-mit-mirror nil
  "Non-nil means `tetris-mit-local' always mirrors to a display server."
  :type 'boolean)

(defcustom tetris-mit-verify-digests t
  "Non-nil means check the SPEC §9.4 digest of every frame received."
  :type 'boolean)

(defcustom tetris-mit-cell-string "  "
  "Text drawn for one window of the facade.
Two spaces look roughly square in most fonts."
  :type 'string)

(defcustom tetris-mit-gatekeeper-endpoint nil
  "Endpoint of an OUTER reservation gatekeeper, or nil.
This package never interprets it.  It only passes the value to
`tetris-mit-gatekeeper-function'.  The reservation system is a
separate project, and this package contains no reservation logic."
  :type '(choice (const :tag "None" nil) string))

(defcustom tetris-mit-gatekeeper-token-source nil
  "Where the gatekeeper's token comes from.
The value is nil, a string, or a function of no arguments that returns
a string.  It is only read when `tetris-mit-gatekeeper-function' is set."
  :type '(choice (const :tag "None" nil) string function))

(defcustom tetris-mit-gatekeeper-function nil
  "Hook for an OUTER reservation gatekeeper that fronts servers, or nil.
When non-nil, it is called before live connections: remote play, the
display and the local mirror.  It gets one plist argument with the keys
:endpoint, :token, :host, :port, :role and :identity (the display
identity alist, or nil).  It returns (HOST . PORT) to connect to, such
as the gatekeeper's proxy, or nil to connect directly.  The protocol
is unchanged; see docs/PROTOCOL.md."
  :type '(choice (const :tag "Direct connections" nil) function))

(defconst tetris-mit-rows 17 "Display rows (SPEC §2.1).")
(defconst tetris-mit-cols 9 "Display columns (SPEC §2.1).")
(defconst tetris-mit-fps 30 "Frames per second (SPEC §2.3).")
(defconst tetris-mit-protocol "17x9-tetris-remote" "Protocol name.")
(defconst tetris-mit-protocol-version 1 "Protocol version: contract v1.")

(defcustom tetris-mit-protocol-versions '(1 0)
  "Protocol versions a hello offers, most preferred first.
A connection says hello with the first.  If the server refuses it
with a `version' error before its own hello, the connection is opened
again with the next.  Contract v1 servers speak 1 only, and the draft
v0 server speaks 0 only, so with the default both work, and a v1
server never sees a v0 hello."
  :type '(repeat natnum))

(defcustom tetris-mit-hello-timeout 5
  "Seconds to wait for the server's first message, to settle the version.
Only a hello that has a fallback in `tetris-mit-protocol-versions'
waits."
  :type 'number)
(defconst tetris-mit-max-message 65536
  "Largest protocol message in bytes, including its newline.")
(defconst tetris-mit-max-tick 3600 "Largest `frames' of one tick message.")

(eval-and-compile
  (defconst tetris-mit-actions
    '("left" "right" "soft_drop" "hard_drop"
      "rotate_cw" "rotate_ccw" "rotate_180" "hold")
    "The SPEC §5.1 action names.")

  (defun tetris-mit--action-command (action)
    "The `tetris-mit-remote-mode' command that sends ACTION."
    (intern (concat "tetris-mit-remote-" (string-replace "_" "-" action)))))

(defconst tetris-mit-palette
  '(("." 0 0 0) ("W" 255 255 255) ("I" 0 255 255) ("J" 0 0 255)
    ("L" 255 170 0) ("O" 255 255 0) ("S" 0 255 0) ("Z" 255 0 0)
    ("T" 153 0 255) ("G" 42 42 42))
  "The SPEC §4.1 palette, as (CODE R G B) entries.")

(defun tetris-mit-palette-rgb (code)
  "The (R G B) list for palette CODE, a one-letter string."
  (cdr (assoc code tetris-mit-palette)))

(defun tetris-mit-rgb-hex (cell)
  "CELL, an [R G B] vector, as a \"#rrggbb\" color string."
  (format "#%02x%02x%02x" (aref cell 0) (aref cell 1) (aref cell 2)))

;;;; Codec

(define-error 'tetris-mit-protocol-error "17x9 Tetris protocol error")

(defun tetris-mit--protocol-error (code format-string &rest args)
  "Signal `tetris-mit-protocol-error' with CODE and a message.
The message is made from FORMAT-STRING and ARGS."
  (signal 'tetris-mit-protocol-error
          (list code (apply #'format-message format-string args))))

(defun tetris-mit-make-hello (role &optional fields version)
  "A hello message for ROLE: \"controller\", \"viewer\" or \"producer\".
FIELDS is an alist of extra fields, such as ((seed . 42)).  A field
that is already present, such as `client', is replaced rather than
repeated, because the server rejects duplicate keys.  VERSION is the
protocol version, by default the first of `tetris-mit-protocol-versions'."
  (let ((msg (list (cons 'type "hello") (cons 'protocol tetris-mit-protocol)
                   (cons 'version (or version (car tetris-mit-protocol-versions)))
                   (cons 'role role) (cons 'client "tetris-mit.el"))))
    (dolist (field fields msg)
      (let ((cell (assq (car field) msg)))
        (if cell
            (setcdr cell (cdr field))
          (setq msg (append msg (list (cons (car field) (cdr field))))))))))

(defun tetris-mit-make-event (action down)
  "An event message: press ACTION if DOWN is non-nil, else release it."
  (unless (member action tetris-mit-actions)
    (error "Unknown SPEC action %S" action))
  `((type . "event") (action . ,action) (down . ,(if down t :json-false))))

(defun tetris-mit-make-tick (frames)
  "A tick message advancing a lockstep engine by FRAMES frames."
  `((type . "tick") (frames . ,frames)))

(defun tetris-mit-make-frame (frame-no rows)
  "A frame message for ROWS, numbered FRAME-NO, with its digest."
  `((type . "frame") (frame_no . ,frame-no) (rows . ,rows)
    (digest . ,(tetris-mit-frame-digest rows))))

(defun tetris-mit-make-state (score level lines)
  "A state message for SCORE, LEVEL and LINES."
  `((type . "state") (score . ,score) (level . ,level) (lines . ,lines)))

(defun tetris-mit-make-ping (&optional id)
  "A ping message, with ID if non-nil."
  (if id `((type . "ping") (id . ,id)) '((type . "ping"))))

(defun tetris-mit-encode (msg)
  "Encode MSG, an alist with symbol keys, as one protocol line.
Booleans are t and :json-false, and arrays are vectors."
  (let* ((json-encoding-pretty-print nil)
         (line (concat (json-encode msg) "\n")))
    (when (> (string-bytes line) tetris-mit-max-message)
      (tetris-mit--protocol-error "too_large" "%d bytes > %d"
                                  (string-bytes line) tetris-mit-max-message))
    line))

(defun tetris-mit--json-parse (string)
  "Parse the JSON in STRING: objects as alists, arrays as vectors."
  (if (and (fboundp 'json-parse-string)
           (or (not (fboundp 'json-available-p)) (json-available-p)))
      (json-parse-string string :object-type 'alist :array-type 'array
                         :null-object nil :false-object :json-false)
    (let ((json-object-type 'alist) (json-array-type 'vector)
          (json-key-type 'symbol) (json-false :json-false) (json-null nil))
      (json-read-from-string string))))

(defun tetris-mit-valid-rows-p (rows)
  "Non-nil if ROWS is a SPEC frame.
A frame is 17 vectors of 9 [R G B] vectors of integers in 0..255."
  (and (vectorp rows) (= (length rows) tetris-mit-rows)
       (cl-every
        (lambda (row)
          (and (vectorp row) (= (length row) tetris-mit-cols)
               (cl-every (lambda (cell)
                           (and (vectorp cell) (= (length cell) 3)
                                (cl-every (lambda (v) (and (integerp v) (<= 0 v 255)))
                                          cell)))
                         row)))
        rows)))

(defun tetris-mit-frame-bytes (rows)
  "The SPEC §9.4 bytes of ROWS: 459 bytes, row-major R, G, B."
  (let (bytes)
    (dotimes (r tetris-mit-rows)
      (dotimes (c tetris-mit-cols)
        (let ((cell (aref (aref rows r) c)))
          (push (aref cell 0) bytes)
          (push (aref cell 1) bytes)
          (push (aref cell 2) bytes))))
    (apply #'unibyte-string (nreverse bytes))))

(defun tetris-mit-frame-digest (rows)
  "The SPEC §9.4 digest of ROWS: lowercase hex SHA-256 of its bytes."
  (secure-hash 'sha256 (tetris-mit-frame-bytes rows)))

(defun tetris-mit--check-frame (msg)
  "Signal a protocol error unless MSG is a valid frame message."
  (let ((rows (alist-get 'rows msg))
        (digest (alist-get 'digest msg)))
    (unless (natnump (alist-get 'frame_no msg))
      (tetris-mit--protocol-error "bad_frame" "frame_no must be an integer >= 0"))
    (unless (tetris-mit-valid-rows-p rows)
      (tetris-mit--protocol-error
       "bad_frame" "a frame is 17 rows of 9 [r, g, b] integers in 0..255"))
    (when (and digest tetris-mit-verify-digests
               (not (equal digest (tetris-mit-frame-digest rows))))
      (tetris-mit--protocol-error "digest" "digest does not match rows"))
    (unless (tetris-mit--valid-step-events-p (alist-get 'events msg))
      (tetris-mit--protocol-error
       "bad_frame" "events must be an array of [action, down] pairs"))))

(defun tetris-mit--valid-step-events-p (events)
  "Non-nil if EVENTS is absent (nil) or a frame's valid `events' (E_k).
That is a vector of [ACTION DOWN] pairs, with SPEC actions and booleans."
  (or (null events)
      (and (vectorp events)
           (cl-every (lambda (e)
                       (and (vectorp e) (= (length e) 2)
                            (member (aref e 0) tetris-mit-actions)
                            (memq (aref e 1) '(t :json-false))))
                     events))))

(defun tetris-mit-decode (line)
  "Decode LINE, one protocol message, into an alist with symbol keys.
Signal `tetris-mit-protocol-error', with a docs/PROTOCOL.md §6 code, if
LINE is oversized, malformed or invalid.  A frame must be 17x9 cells of
three integers in 0..255.  When `tetris-mit-verify-digests' is non-nil,
its digest must also match."
  (when (> (string-bytes line) tetris-mit-max-message)
    (tetris-mit--protocol-error "too_large" "%d bytes > %d"
                                (string-bytes line) tetris-mit-max-message))
  (let ((msg (condition-case err
                 (tetris-mit--json-parse (string-trim-right line "[\r\n]+"))
               (error (tetris-mit--protocol-error
                       "malformed" "%s" (error-message-string err))))))
    (unless (and (consp msg) (consp (car msg))
                 (stringp (alist-get 'type msg)))
      (tetris-mit--protocol-error "malformed"
                                  "a message is an object with a string type"))
    (pcase (alist-get 'type msg)
      ("frame" (tetris-mit--check-frame msg))
      ("state"
       (dolist (key '(score level lines))
         (unless (natnump (alist-get key msg))
           (tetris-mit--protocol-error "bad_state" "%s must be an integer >= 0"
                                       key))))
      ("event"
       (unless (member (alist-get 'action msg) tetris-mit-actions)
         (tetris-mit--protocol-error "bad_event" "unknown action %S"
                                     (alist-get 'action msg)))
       (unless (memq (alist-get 'down msg) '(t :json-false))
         (tetris-mit--protocol-error "bad_event" "down must be true or false")))
      ("hello"
       (unless (and (equal (alist-get 'protocol msg) tetris-mit-protocol)
                    (integerp (alist-get 'version msg))
                    (memql (alist-get 'version msg) tetris-mit-protocol-versions))
         (tetris-mit--protocol-error "version" "expected %s version %s"
                                     tetris-mit-protocol
                                     (mapconcat #'number-to-string
                                                tetris-mit-protocol-versions
                                                " or "))))
      ((or "tick" "ping" "pong" "error") nil)
      (type (tetris-mit--protocol-error "unknown_type" "unknown type %S" type)))
    msg))

;;;; Connections

(defun tetris-mit--connect (name host port role hello-fields version)
  "Open NAME to HOST:PORT and say hello as ROLE in protocol VERSION.
Messages are held in the process's queue until `tetris-mit--release'."
  (let ((proc (open-network-stream name nil host port
                                   :type 'plain :coding 'utf-8)))
    (set-process-query-on-exit-flag proc nil)
    (process-put proc 'tetris-mit-pending "")
    (process-put proc 'tetris-mit-held t)
    (process-put proc 'tetris-mit-queue nil)
    (process-put proc 'tetris-mit-role role)
    (process-put proc 'tetris-mit-version version)
    (set-process-filter proc #'tetris-mit--filter)
    (set-process-sentinel proc #'tetris-mit--sentinel)
    (tetris-mit-send proc (tetris-mit-make-hello role hello-fields version))
    proc))

(defun tetris-mit--version-refused-p (proc)
  "Non-nil if the server refuses PROC's hello with a `version' error.
Wait up to `tetris-mit-hello-timeout' seconds for its first message."
  (tetris-mit--wait-until (lambda () (or (process-get proc 'tetris-mit-queue)
                                         (not (process-live-p proc))))
                          tetris-mit-hello-timeout)
  (let ((first (car (process-get proc 'tetris-mit-queue))))
    (and (equal (alist-get 'type first) "error")
         (equal (alist-get 'code first) "version")
         (not (alist-get 'local first)))))

(defun tetris-mit--handle (proc msg)
  "Pass MSG to PROC's handler, noting the role a server hello accepted."
  (when (equal (alist-get 'type msg) "hello")
    ;; Contract v1: act on client_role, not on the role asked for (§2).
    (process-put proc 'tetris-mit-client-role
                 (or (alist-get 'client_role msg) (process-get proc 'tetris-mit-role))))
  (funcall (process-get proc 'tetris-mit-handler) proc msg))

(defun tetris-mit--dispatch (proc msg)
  "Hold MSG in PROC's queue, or pass it on once PROC is released."
  (if (process-get proc 'tetris-mit-held)
      (process-put proc 'tetris-mit-queue
                   (nconc (process-get proc 'tetris-mit-queue) (list msg)))
    (tetris-mit--handle proc msg)))

(defun tetris-mit--release (proc)
  "Pass PROC's held messages to its handler, in order, then stop holding."
  (let (queue)
    (while (setq queue (process-get proc 'tetris-mit-queue))
      (process-put proc 'tetris-mit-queue (cdr queue))
      (tetris-mit--handle proc (car queue))))
  (process-put proc 'tetris-mit-held nil))

(defun tetris-mit-open (name host port role handler &optional hello-fields)
  "Connect to the protocol server at HOST:PORT as ROLE, and say hello.
NAME names the network process.  HELLO-FIELDS is an alist of extra
hello fields.  Each decoded message is passed to HANDLER as
\(HANDLER PROCESS MESSAGE).  A message that fails to decode arrives
as an error message with (local . t).  When the connection closes,
HANDLER gets a message of type \"closed\".  Return the process.

The hello offers the versions of `tetris-mit-protocol-versions' in
order.  If the server refuses one with a `version' error before its
own hello, the connection is opened again with the next version; the
refused attempt never reaches HANDLER.  The process property
`tetris-mit-version' is the version spoken.  `tetris-mit-client-role'
returns the role the server accepted, which a gatekeeper may have
demoted (contract v1, §2 and §9.3).  Messages that arrive while the
version is settled reach HANDLER from a timer, after this function
has returned, so that the caller can store the process first."
  (let ((versions tetris-mit-protocol-versions) proc)
    (while (progn
             (setq proc (tetris-mit--connect name host port role hello-fields
                                             (pop versions)))
             (and versions (tetris-mit--version-refused-p proc)))
      (delete-process proc))
    (process-put proc 'tetris-mit-handler handler)
    (if (process-get proc 'tetris-mit-queue)
        (run-at-time 0 nil #'tetris-mit--release proc)
      (process-put proc 'tetris-mit-held nil))
    proc))

(defun tetris-mit-client-role (proc)
  "The role the server accepted for PROC, from its hello's `client_role'.
It is nil before the server's hello.  A v0 server sends no
`client_role'; the role asked for is then assumed."
  (process-get proc 'tetris-mit-client-role))

(defun tetris-mit--check-controller (proc)
  "Signal a `user-error' if the server admitted PROC as anything but a controller."
  (let ((role (and proc (tetris-mit-client-role proc))))
    (when (and role (not (equal role "controller")))
      (user-error "tetris-mit: the server admitted this connection as a %s, \
so it cannot play" role))))

(defun tetris-mit-gatekeeper-token ()
  "The token from `tetris-mit-gatekeeper-token-source', or nil."
  (let ((source tetris-mit-gatekeeper-token-source))
    (cond ((functionp source) (funcall source))
          ((stringp source) source))))

(defun tetris-mit-resolve-endpoint (host port role &optional identity)
  "Where to connect for HOST:PORT as ROLE: (HOST . PORT).
Ask `tetris-mit-gatekeeper-function', if it is set, and pass it
IDENTITY.  Otherwise, or when it returns nil, return HOST:PORT."
  (or (and tetris-mit-gatekeeper-function
           (funcall tetris-mit-gatekeeper-function
                    (list :endpoint tetris-mit-gatekeeper-endpoint
                          :token (tetris-mit-gatekeeper-token)
                          :host host :port port :role role :identity identity)))
      (cons host port)))

(defun tetris-mit-send (proc &rest msgs)
  "Send MSGS to the network process PROC as protocol lines, in one write."
  (unless (process-live-p proc)
    (user-error "tetris-mit: not connected"))
  (process-send-string proc (mapconcat #'tetris-mit-encode msgs "")))

(defun tetris-mit--deliver (proc line)
  "Decode LINE from PROC and pass it to PROC's handler."
  (unless (string-blank-p line)
    (let ((msg (condition-case err
                   (tetris-mit-decode line)
                 (tetris-mit-protocol-error
                  `((type . "error") (code . ,(nth 1 err))
                    (message . ,(nth 2 err)) (local . t))))))
      (tetris-mit--dispatch proc msg))))

(defun tetris-mit--filter (proc chunk)
  "Split the output CHUNK of PROC into lines and deliver each one."
  (let ((data (concat (process-get proc 'tetris-mit-pending) chunk))
        (start 0) newline)
    (process-put proc 'tetris-mit-pending "")
    (while (setq newline (string-search "\n" data start))
      (tetris-mit--deliver proc (substring data start newline))
      (setq start (1+ newline)))
    (let ((rest (substring data start)))
      (if (<= (string-bytes rest) tetris-mit-max-message)
          (process-put proc 'tetris-mit-pending rest)
        (tetris-mit--dispatch proc '((type . "error") (code . "too_large") (local . t)
                                     (message . "message over 65536 bytes")))
        (delete-process proc)))))

(defun tetris-mit--sentinel (proc event)
  "Tell PROC's handler that the connection closed, with EVENT."
  (unless (process-live-p proc)
    (tetris-mit--dispatch proc `((type . "closed") (message . ,(string-trim event))))))

(defun tetris-mit--wait-until (predicate timeout)
  "Process input until PREDICATE returns non-nil, or TIMEOUT seconds pass.
Return the value of PREDICATE."
  (let ((deadline (+ (float-time) timeout)) value)
    (while (and (not (setq value (funcall predicate)))
                (< (float-time) deadline))
      (accept-process-output nil 0.05))
    value))

(defun tetris-mit-start-server (&rest args)
  "Start \"python -m tetris_sim.server ARGS\" on 127.0.0.1, on a free port.
Use `tetris-mit-python' and the checkout at `tetris-mit-root'.  Wait
until the server listens, and return (PROCESS . PORT)."
  (let* ((root tetris-mit-root)
         (process-environment
          (cons (concat "PYTHONPATH="
                        (expand-file-name "impl/python/engine" root) path-separator
                        (expand-file-name "impl/python/sim" root))
                process-environment))
         (buffer (generate-new-buffer " *tetris-mit-server*"))
         (proc (make-process
                :name "tetris-mit-server" :buffer buffer :noquery t
                :connection-type 'pipe
                :command (append (list tetris-mit-python "-m" "tetris_sim.server"
                                       "--host" "127.0.0.1" "--port" "0")
                                 args)))
         (port-of (lambda ()
                    (with-current-buffer buffer
                      (save-excursion
                        (goto-char (point-min))
                        ;; The host may differ: a FreeBSD jail maps
                        ;; 127.0.0.1 to its lo0 address.
                        (and (re-search-forward
                              "listening on [^ ]+:\\([0-9]+\\)" nil t)
                             (string-to-number (match-string 1)))))))
         (port (tetris-mit--wait-until
                (lambda () (or (funcall port-of) (not (process-live-p proc))))
                30)))
    (setq port (funcall port-of))
    (unless port
      (let ((output (with-current-buffer buffer (buffer-string))))
        (when (process-live-p proc) (delete-process proc))
        (kill-buffer buffer)
        (error "tetris-mit: the server did not start: %s" output)))
    (cons proc port)))

(defun tetris-mit-stop-server (server)
  "Stop SERVER, from `tetris-mit-start-server', and return its output."
  (let ((proc (car server)))
    (when (process-live-p proc)
      (signal-process proc 'SIGTERM)
      (tetris-mit--wait-until (lambda () (not (process-live-p proc))) 10)
      (when (process-live-p proc) (delete-process proc)))
    ;; A process can be dead before Emacs has read the last of its
    ;; output; waiting on it reads what is left in the pipe.
    (accept-process-output proc 0.1)
    (let* ((buffer (process-buffer proc))
           (output (and (buffer-live-p buffer)
                        (with-current-buffer buffer (buffer-string)))))
      (when (buffer-live-p buffer) (kill-buffer buffer))
      output)))

;;;; Rendering

(defvar tetris-mit--faces (make-hash-table :test #'equal)
  "Face specs, (:background HEX), keyed by color.")

(defun tetris-mit--cell-face (cell)
  "The face for a window of color CELL, an [R G B] vector."
  (let ((hex (tetris-mit-rgb-hex cell)))
    (or (gethash hex tetris-mit--faces)
        (puthash hex (list :background hex) tetris-mit--faces))))

(defun tetris-mit-insert-grid (rows &optional prefix)
  "Insert the frame ROWS at point: 17 lines of colored cells.
Each line starts with PREFIX, if non-nil."
  (dotimes (r tetris-mit-rows)
    (when prefix (insert prefix))
    (let ((row (aref rows r)))
      (dotimes (c tetris-mit-cols)
        (insert (propertize tetris-mit-cell-string
                            'face (tetris-mit--cell-face (aref row c))))))
    (insert "\n")))

(defun tetris-mit-render-frame (rows &optional status)
  "Replace the current buffer's text with the frame ROWS.
Each of the 17x9 windows is `tetris-mit-cell-string', with the window's
color as its face background.  STATUS, if non-nil, is a string shown
below the grid after a blank line."
  (let ((inhibit-read-only t))
    (erase-buffer)
    (tetris-mit-insert-grid rows)
    (when status (insert "\n" status "\n"))
    (goto-char (point-min))))

(defun tetris-mit-status-string (state &optional frame-no note)
  "A status line for the state message STATE.
FRAME-NO and NOTE are added when non-nil."
  (concat (format "Score %d  Level %d  Lines %d"
                  (or (alist-get 'score state) 0) (or (alist-get 'level state) 0)
                  (or (alist-get 'lines state) 0))
          (let ((high (alist-get 'high_score state)))
            (if high (format "  High %d" high) ""))
          (let ((phase (alist-get 'phase state)))
            (if phase (format "  [%s]" phase) ""))
          (if frame-no (format "  frame %d" frame-no) "")
          (if note (format "  (%s)" note) "")))

(defconst tetris-mit--black-rows
  (make-vector tetris-mit-rows (make-vector tetris-mit-cols [0 0 0]))
  "An all-black frame.")

;;;; Remote play: SPEC v1, the engine decides

(defconst tetris-mit-remote-buffer-name "*Tetris MIT*"
  "Buffer of `tetris-mit-remote'.")

(defvar tetris-mit-remote-frame-functions nil
  "Abnormal hook run with each frame message `tetris-mit-remote' receives.")

(defvar-local tetris-mit--process nil "Connection to the engine server.")
(defvar-local tetris-mit--host nil "Host of the engine server.")
(defvar-local tetris-mit--port nil "Port of the engine server.")
(defvar-local tetris-mit--server nil "The server's hello message.")
(defvar-local tetris-mit--rows nil "The last frame received.")
(defvar-local tetris-mit--digest nil "Digest of the last frame received.")
(defvar-local tetris-mit--drawn-digest nil "Digest of the frame on screen.")
(defvar-local tetris-mit--frame-no nil "Number of the last frame received.")
(defvar-local tetris-mit--state nil "The last state message received.")
(defvar-local tetris-mit--note nil "Connection note for the status line.")
(defvar-local tetris-mit--last-error nil "The last error message received.")

(defun tetris-mit-action-events (action)
  "The messages for one key press of ACTION: a press, then a release.
Emacs cannot see key releases, so every key is a tap (PROTOCOL.md §4.4)."
  (list (tetris-mit-make-event action t) (tetris-mit-make-event action nil)))

(defun tetris-mit-remote-tap (action)
  "Send ACTION to the engine as a key tap: a press, then a release.
Refuse if the server admitted this connection as a viewer."
  (tetris-mit--check-controller tetris-mit--process)
  (apply #'tetris-mit-send tetris-mit--process (tetris-mit-action-events action)))

(defmacro tetris-mit--define-action-commands ()
  "Define one `tetris-mit-remote-mode' command per SPEC action."
  `(progn
     ,@(mapcar (lambda (action)
                 `(defun ,(tetris-mit--action-command action) ()
                    ,(format "Send the SPEC action `%s' (press, then release)."
                             action)
                    (interactive nil tetris-mit-remote-mode)
                    (tetris-mit-remote-tap ,action)))
               tetris-mit-actions)))

(tetris-mit--define-action-commands)

(defvar tetris-mit-remote-mode-map
  (let ((map (make-sparse-keymap)))
    (define-key map "q" #'tetris-mit-remote-quit)
    (define-key map "g" #'tetris-mit-remote-reconnect)
    (define-key map "." #'tetris-mit-remote-tick)
    map)
  "Keymap of `tetris-mit-remote-mode'; see `tetris-mit-remote-bindings'.")

(defcustom tetris-mit-remote-bindings
  '(("<left>" . "left") ("<right>" . "right") ("<down>" . "soft_drop")
    ("SPC" . "hard_drop") ("<up>" . "rotate_cw") ("c" . "rotate_cw")
    ("z" . "rotate_ccw") ("x" . "rotate_180")
    ("S-SPC" . "hold") ("TAB" . "hold") ("<backtab>" . "hold") ("h" . "hold"))
  "Keys of `tetris-mit-remote-mode', and the SPEC action each one sends.
Keys are `kbd' strings.  The defaults follow SPEC §14's legacy
keyboard table.  Emacs receives keys, not physical key-downs, so a
bare Shift press never reaches it.  Hold is therefore on S-SPC (in
graphical frames), TAB, S-TAB and h."
  :type '(alist :key-type (string :tag "Key")
                :value-type (choice (const "left") (const "right")
                                    (const "soft_drop") (const "hard_drop")
                                    (const "rotate_cw") (const "rotate_ccw")
                                    (const "rotate_180") (const "hold")))
  :set (lambda (symbol value)
         (set-default symbol value)
         (when (fboundp 'tetris-mit--populate-remote-map)
           (tetris-mit--populate-remote-map))))

(defun tetris-mit--populate-remote-map ()
  "Bind the keys of `tetris-mit-remote-bindings' in the remote keymap."
  (dolist (binding tetris-mit-remote-bindings)
    (define-key tetris-mit-remote-mode-map (kbd (car binding))
                (tetris-mit--action-command (cdr binding)))))

(tetris-mit--populate-remote-map)

(define-derived-mode tetris-mit-remote-mode special-mode "Tetris-MIT"
  "Play SPEC v1 17x9 Tetris on a remote engine server.
The server runs the engine, and this buffer only shows its frames and
sends keys as SPEC actions.

\\{tetris-mit-remote-mode-map}"
  (setq-local truncate-lines t)
  (setq-local cursor-type nil)
  (setq-local show-trailing-whitespace nil)
  (buffer-disable-undo)
  (add-hook 'kill-buffer-hook #'tetris-mit-remote-disconnect nil t))

(defun tetris-mit--remote-redraw ()
  "Show the last frame and the status line."
  (let ((inhibit-read-only t)
        (status (tetris-mit-status-string tetris-mit--state tetris-mit--frame-no
                                          tetris-mit--note)))
    (if (and tetris-mit--rows (> (buffer-size) 0)
             (equal tetris-mit--digest tetris-mit--drawn-digest))
        (save-excursion                 ; same frame: just the status line
          (goto-char (point-min))
          (forward-line (1+ tetris-mit-rows))
          (delete-region (point) (point-max))
          (insert status "\n"))
      (tetris-mit-render-frame (or tetris-mit--rows tetris-mit--black-rows)
                               status)
      (setq tetris-mit--drawn-digest tetris-mit--digest))))

(defun tetris-mit--remote-receive (msg)
  "Handle MSG from the engine server in the remote buffer."
  (pcase (alist-get 'type msg)
    ("hello"
     (setq tetris-mit--server msg)
     (setq tetris-mit--note
           (cond ((not (equal (alist-get 'mode msg) "engine"))
                  (format "server is in %s mode; use --mode engine"
                          (alist-get 'mode msg)))
                 ;; Contract v1: a gatekeeper may demote the controller.
                 ((not (member (alist-get 'client_role msg) '(nil "controller")))
                  (format "admitted as %s: keys are off; %s clock, seed %s"
                          (alist-get 'client_role msg) (alist-get 'clock msg)
                          (alist-get 'seed msg)))
                 (t (format "%s clock, seed %s" (alist-get 'clock msg)
                            (alist-get 'seed msg)))))
     (tetris-mit--remote-redraw))
    ("frame"
     (setq tetris-mit--rows (alist-get 'rows msg)
           tetris-mit--digest (alist-get 'digest msg)
           tetris-mit--frame-no (alist-get 'frame_no msg))
     (run-hook-with-args 'tetris-mit-remote-frame-functions msg)
     (tetris-mit--remote-redraw))
    ("state"
     (setq tetris-mit--state msg)
     (tetris-mit--remote-redraw))
    ("error"
     (setq tetris-mit--last-error msg)
     (message "tetris-mit: %s: %s" (alist-get 'code msg) (alist-get 'message msg)))
    ("closed"
     (setq tetris-mit--process nil)
     (setq tetris-mit--note (format "disconnected: %s" (alist-get 'message msg)))
     (tetris-mit--remote-redraw))))

;;;###autoload
(defun tetris-mit-remote (&optional host port)
  "Play SPEC v1 17x9 Tetris on the engine server at HOST:PORT.
This mode is SPEC-faithful: the server runs the SPEC engine
\(python -m tetris_sim.server --mode engine), and Emacs only sends
key taps as SPEC actions and draws the frames it gets back.  Every
cell is colored with a face background, and the status line shows
score, level and lines.  With a prefix argument, ask for HOST and
PORT.  Otherwise use `tetris-mit-host' and `tetris-mit-port'.

\\<tetris-mit-remote-mode-map>Keys: \\[tetris-mit-remote-left] / \\[tetris-mit-remote-right] move, \\[tetris-mit-remote-soft-drop] soft drop, \\[tetris-mit-remote-hard-drop] hard drop,
\\[tetris-mit-remote-rotate-cw] rotate clockwise, \\[tetris-mit-remote-rotate-ccw] counter-clockwise, \\[tetris-mit-remote-rotate-180] 180, \\[tetris-mit-remote-hold] hold,
\\[tetris-mit-remote-reconnect] reconnect, \\[tetris-mit-remote-quit] quit."
  (interactive
   (if current-prefix-arg
       (list (read-string "Engine host: " tetris-mit-host)
             (read-number "Engine port: " tetris-mit-port))
     (list tetris-mit-host tetris-mit-port)))
  (let ((host (or host tetris-mit-host))
        (port (or port tetris-mit-port))
        (buffer (get-buffer-create tetris-mit-remote-buffer-name)))
    (with-current-buffer buffer
      (tetris-mit-remote-disconnect)
      (tetris-mit-remote-mode)
      (setq tetris-mit--host host
            tetris-mit--port port)
      (setq tetris-mit--process
            (let ((target (tetris-mit-resolve-endpoint host port "controller")))
              (tetris-mit-open "tetris-mit-remote" (car target) (cdr target)
                               "controller"
                               (lambda (proc msg)
                                 (when (buffer-live-p buffer)
                                   (with-current-buffer buffer
                                     (when (eq proc tetris-mit--process)
                                       (tetris-mit--remote-receive msg))))))))
      (setq tetris-mit--note "connecting")
      (tetris-mit--remote-redraw))
    (pop-to-buffer-same-window buffer)
    buffer))

(defun tetris-mit-remote-disconnect ()
  "Close the connection to the engine server."
  (interactive nil tetris-mit-remote-mode)
  (let ((proc tetris-mit--process))
    (setq tetris-mit--process nil)
    (when (process-live-p proc)
      (delete-process proc))))

(defun tetris-mit-remote-quit ()
  "Disconnect from the engine server and quit the window."
  (interactive nil tetris-mit-remote-mode)
  (tetris-mit-remote-disconnect)
  (quit-window))

(defun tetris-mit-remote-reconnect ()
  "Connect to the same engine server again, which starts a new game."
  (interactive nil tetris-mit-remote-mode)
  (tetris-mit-remote tetris-mit--host tetris-mit--port))

(defun tetris-mit-remote-tick (frames)
  "Advance a lockstep engine server by FRAMES frames (the prefix argument)."
  (interactive "p" tetris-mit-remote-mode)
  (tetris-mit--check-controller tetris-mit--process)
  (tetris-mit-send tetris-mit--process (tetris-mit-make-tick frames)))

;;;; Known-answer vectors: the test driver

(defcustom tetris-mit-kav-window 600
  "Most frames a KAV replay has ticked but not yet received."
  :type 'natnum)

(defvar tetris-mit-kav-print nil
  "Non-nil means KAV results are also printed to standard output.")

(defun tetris-mit-trace-directory ()
  "The directory of the sealed conformance traces (the KAVs)."
  (expand-file-name "spec/conformance/traces/" tetris-mit-root))

(defun tetris-mit-read-trace (file)
  "Read the conformance trace FILE in place, without copying it."
  (with-temp-buffer
    (insert-file-contents file)
    (tetris-mit--json-parse (buffer-string))))

(defun tetris-mit-kav-id (file)
  "The KAV-NN identifier of the trace FILE, such as KAV-08 for 08-hold.json."
  (let ((name (file-name-base file)))
    (format "KAV-%s" (if (string-match "\\`\\([0-9]+\\)-" name)
                         (match-string 1 name)
                       name))))

(defun tetris-mit--kav-schedule (events frames)
  "Split a trace's EVENTS over FRAMES frames into lockstep steps.
Return a list of (EVENTS . N): send EVENTS, then tick N frames.  The
events of frame k are sent just before the tick that runs frame k."
  (let ((i 0) (n (length events)) (k 0) steps)
    (while (< k frames)
      (let (batch)
        (while (and (< i n) (<= (aref (aref events i) 0) k))
          (push (aref events i) batch)
          (cl-incf i))
        (let* ((next (if (< i n) (min (aref (aref events i) 0) frames) frames))
               (ticks (min (max 1 (- next k)) tetris-mit-max-tick)))
          (push (cons (nreverse batch) ticks) steps)
          (setq k (+ k ticks)))))
    (nreverse steps)))

(defun tetris-mit--event-down-p (value)
  "Non-nil if VALUE, from a trace or from Lisp, means a press."
  (and value (not (eq value :json-false))))

(defun tetris-mit--trace-step-events (events frames)
  "E_k of a trace, for each frame k below FRAMES, as a vector of lists.
EVENTS is the trace's vector of [FRAME ACTION DOWN].  Each E_k is a
list of (ACTION . DOWN), in trace order, with DOWN t or nil."
  (let ((steps (make-vector frames nil)))
    (cl-loop for e across events
             for k = (aref e 0)
             when (< k frames)
             do (push (cons (aref e 1) (tetris-mit--event-down-p (aref e 2)))
                      (aref steps k)))
    (dotimes (k frames) (aset steps k (nreverse (aref steps k))))
    steps))

(defun tetris-mit--frame-step-events (events)
  "A frame message's EVENTS, [[ACTION DOWN] ...], as a list of (ACTION . DOWN)."
  (mapcar (lambda (e) (cons (aref e 0) (tetris-mit--event-down-p (aref e 1))))
          events))

(cl-defun tetris-mit-lockstep-run (host port seed frames events on-frame
                                        &key on-state progress
                                        (name "tetris-mit-lockstep"))
  "Play EVENTS for FRAMES frames from SEED on the lockstep server HOST:PORT.
Connect as the controller, with SEED in the hello.  EVENTS is a vector
of [FRAME ACTION DOWN] entries sorted by frame, as in a conformance
trace; DOWN is t, :json-false or nil.  The events of frame k are sent
just before the tick that runs frame k.  Call ON-FRAME with each frame
message, in order, and ON-STATE, if non-nil, with each state message.
PROGRESS, if non-nil, is called with (RECEIVED FRAMES) from time to
time.  Return a plist (:received N :error MESSAGE-OR-NIL :hello HELLO)."
  (let* ((received 0) (hello nil) (failure nil) (proc nil) (finished nil)
         (tries 0)
         (handler
          (lambda (p msg)
            (when (eq p proc)
              (pcase (alist-get 'type msg)
                ("hello" (setq hello msg))
                ("frame"
                 (let ((k (alist-get 'frame_no msg)))
                   (if (not (eql k received))
                       (setq failure (format "frame_no %s, expected %d" k received))
                     (setq received (1+ received))
                     (condition-case err
                         (funcall on-frame msg)
                       (error (setq failure (format "on-frame: %s"
                                                    (error-message-string err))))))))
                ("state" (when on-state (funcall on-state msg)))
                ("error"
                 (setq failure (format "%s: %s" (alist-get 'code msg)
                                       (alist-get 'message msg))))
                ("closed"
                 (unless finished
                   (setq failure (or failure "connection closed")))))))))
    (unwind-protect
        (progn
          ;; The previous session may still be closing on the server.
          (while (progn
                   (setq hello nil failure nil)
                   (setq proc (tetris-mit-open name host port "controller" handler
                                               `((seed . ,seed))))
                   (tetris-mit--wait-until (lambda () (or hello failure)) 10)
                   (and (not hello) failure (string-prefix-p "busy" failure)
                        (< (cl-incf tries) 50)))
            (delete-process proc)
            (sleep-for 0.05))
          (cond
           ((not hello)
            (setq failure (or failure "no hello from the server")))
           ;; Contract v1 (§2): act on client_role; a gatekeeper may have
           ;; demoted the controller, and a viewer must not send events.
           ((not (member (alist-get 'client_role hello) '(nil "controller")))
            (setq failure (format "the server admitted the controller as a %s"
                                  (alist-get 'client_role hello))))
           ((not (equal (alist-get 'clock hello) "lockstep"))
            (setq failure (format "server clock is %s; replay needs --clock lockstep"
                                  (alist-get 'clock hello))))
           ((not (eql (alist-get 'seed hello) seed))
            (setq failure (format "the server did not take seed %d" seed)))
           (t
            (let ((sent 0) (shown 0))
              (catch 'stop
                (dolist (step (tetris-mit--kav-schedule events frames))
                  (tetris-mit--wait-until
                   (lambda () (or failure (< (- sent received) tetris-mit-kav-window)))
                   30)
                  (when failure (throw 'stop nil))
                  (apply #'tetris-mit-send proc
                         (append (mapcar (lambda (e)
                                           (tetris-mit-make-event
                                            (aref e 1)
                                            (tetris-mit--event-down-p (aref e 2))))
                                         (car step))
                                 (list (tetris-mit-make-tick (cdr step)))))
                  (setq sent (+ sent (cdr step)))
                  (when (and progress (>= (- received shown) 60))
                    (setq shown received)
                    (funcall progress received frames))))
              (tetris-mit--wait-until (lambda () (or failure (>= received frames)))
                                      60)
              (unless (or failure (>= received frames))
                (setq failure (format "timed out after %d/%d frames"
                                      received frames)))))))
      (setq finished t)
      (when (process-live-p proc) (delete-process proc)))
    (list :received received :error failure :hello hello)))

(defun tetris-mit--kav-log (buffer text)
  "Append TEXT to BUFFER, if it is live."
  (when (buffer-live-p buffer)
    (with-current-buffer buffer
      (let ((inhibit-read-only t))
        (goto-char (point-max))
        (insert text)))))

(defun tetris-mit--kav-drop-progress ()
  "Delete a trailing progress line in the current buffer."
  (goto-char (point-max))
  (when (looking-back "^  frames [0-9]+/[0-9]+\n" (line-beginning-position 0))
    (delete-region (match-beginning 0) (match-end 0))))

(defun tetris-mit--kav-progress (buffer received frames)
  "Show RECEIVED of FRAMES in BUFFER's progress line."
  (when (buffer-live-p buffer)
    (with-current-buffer buffer
      (let ((inhibit-read-only t))
        (tetris-mit--kav-drop-progress)
        (insert (format "  frames %d/%d\n" received frames))))
    (unless noninteractive (redisplay))))

(defun tetris-mit-kav-run (file host port &optional buffer)
  "Replay the known-answer vector FILE through the engine server at HOST:PORT.
The server must use the lockstep clock.  The trace's seed goes in the
controller's hello, and its events and ticks are sent frame by frame.
Every frame received has its digest recomputed from its rows, and the
digests at the trace's `digest_every' positions are compared with the
trace.  Progress and the final frame go to BUFFER, if non-nil.

Return a plist (:id :file :matched :total :pass :error :line).
:line is \"KAV-NN: n/n digests match — PASS\" or \"... — FAIL\"."
  (let* ((trace (tetris-mit-read-trace file))
         (id (tetris-mit-kav-id file))
         (seed (alist-get 'seed trace))
         (frames (alist-get 'frames trace))
         (every (alist-get 'digest_every trace))
         (expected (append (alist-get 'digests trace) nil))
         (steps (tetris-mit--trace-step-events (alist-get 'events trace) frames))
         (got (make-vector frames nil))
         (events-seen 0)
         (events-matched 0)
         (last-rows nil)
         (failure nil))
    (tetris-mit--kav-log
     buffer (format "%s  %s  seed %d, %d frames, %d digests (every %d)\n"
                    id (file-name-nondirectory file) seed frames
                    (length expected) every))
    (setq failure
          (plist-get
           (tetris-mit-lockstep-run
            host port seed frames (alist-get 'events trace)
            (lambda (msg)
              (let ((rows (alist-get 'rows msg))
                    (k (alist-get 'frame_no msg))
                    (events (alist-get 'events msg)))
                (aset got k (tetris-mit-frame-digest rows))
                ;; Contract v1: an engine frame carries E_k, the events the
                ;; server passed to step k.  Compare them with the trace's.
                (when events
                  (setq events-seen (1+ events-seen))
                  (when (equal (tetris-mit--frame-step-events events) (aref steps k))
                    (setq events-matched (1+ events-matched))))
                (setq last-rows rows)))
            :name (concat "tetris-mit-" id)
            :progress (lambda (received total)
                        (tetris-mit--kav-progress buffer received total)))
           :error))
    (let* ((actual (cl-loop for k below frames
                            when (or (zerop (% k every)) (= k (1- frames)))
                            collect (aref got k)))
           (total (length expected))
           (matched (cl-loop for a in actual for e in expected count (equal a e)))
           ;; A v0 server sends no events: then only the digests are checked.
           (events-ok (or (zerop events-seen) (= events-matched frames)))
           (pass (and (not failure) (= matched total) (= (length actual) total)
                      events-ok))
           (line (format "%s: %d/%d digests match%s — %s"
                         id matched total
                         (if (zerop events-seen) ""
                           (format ", %d/%d frame events match" events-matched frames))
                         (if pass "PASS" "FAIL"))))
      (when (buffer-live-p buffer)
        (with-current-buffer buffer
          (let ((inhibit-read-only t))
            (tetris-mit--kav-drop-progress)
            (when last-rows (tetris-mit-insert-grid last-rows "  "))
            (when failure (insert "  error: " failure "\n"))
            (insert (propertize line 'face (if pass 'success 'error)) "\n\n"))))
      (list :id id :file file :matched matched :total total :pass pass
            :error failure :line line))))

(defun tetris-mit-kav-replay (files &optional host port buffer)
  "Replay each trace in FILES as a KAV, and return the result plists.
With PORT, use the lockstep engine server at HOST:PORT.  Otherwise
start a private one with `tetris-mit-start-server', and stop it
afterwards.  BUFFER, if non-nil, shows progress."
  (let* ((server (unless port
                   (tetris-mit-start-server "--mode" "engine" "--clock" "lockstep")))
         (host (or host "127.0.0.1"))
         (port (or port (cdr server))))
    (unwind-protect
        (mapcar (lambda (file)
                  (let ((result (tetris-mit-kav-run file host port buffer)))
                    (when tetris-mit-kav-print
                      (princ (concat (plist-get result :line)
                                     (if (plist-get result :error)
                                         (format "  (%s)" (plist-get result :error))
                                       "")
                                     "\n")))
                    result))
                files)
      (when server (tetris-mit-stop-server server)))))

(define-derived-mode tetris-mit-kav-mode special-mode "Tetris-MIT-KAV"
  "Results of known-answer vector replays (`tetris-mit-kav')."
  (setq-local truncate-lines t))

(defun tetris-mit--kav-summary (results)
  "The summary line for the KAV RESULTS."
  (let ((passed (cl-count-if (lambda (r) (plist-get r :pass)) results)))
    (format "%s: %d/%d KAVs pass" (if (= passed (length results)) "PASS" "FAIL")
            passed (length results))))

;;;###autoload
(defun tetris-mit-kav (files &optional host port)
  "Replay known-answer vectors (conformance traces) through the engine server.
FILES are trace files, from spec/conformance/traces/ by default; a
directory stands for all its traces.  Each replay sends the trace's
seed and events to a lockstep engine server, receives every frame,
and compares every frame digest with the trace.  It then shows
\"KAV-NN: n/n digests match — PASS\" (or FAIL) in *tetris-mit-kav*.
With a prefix argument, use an existing lockstep server at HOST:PORT.
Otherwise start a private one with `tetris-mit-python'.  Return the
results, as for `tetris-mit-kav-replay'."
  (interactive
   (let* ((choice (read-file-name "KAV trace (file or directory): "
                                  (tetris-mit-trace-directory) nil t))
          (files (if (file-directory-p choice)
                     (directory-files choice t "\\.json\\'")
                   (list choice))))
     (if current-prefix-arg
         (list files (read-string "Lockstep server host: " tetris-mit-host)
               (read-number "Lockstep server port: " tetris-mit-port))
       (list files))))
  (let ((buffer (get-buffer-create "*tetris-mit-kav*")))
    (with-current-buffer buffer
      (tetris-mit-kav-mode)
      (let ((inhibit-read-only t)) (erase-buffer)))
    (display-buffer buffer)
    (let* ((results (tetris-mit-kav-replay files host port buffer))
           (summary (tetris-mit--kav-summary results)))
      (tetris-mit--kav-log buffer (concat summary "\n"))
      (message "%s" summary)
      results)))

(defun tetris-mit-kav-batch ()
  "Batch entry point: replay the traces named on the command line.
  emacs --batch -l contrib/emacs/tetris-mit.el -f tetris-mit-kav-batch \\
        [--host H --port P] TRACE.json|DIR ...
Print one KAV line per trace, then a summary.  Exit with 0 if every
KAV passes, 1 if any fails, and 2 on usage or setup errors.  Without
--port, start a private lockstep server with `tetris-mit-python'
\(TETRIS_MIT_PYTHON)."
  (let (host port files)
    (while command-line-args-left
      (let ((arg (pop command-line-args-left)))
        (cond ((equal arg "--host") (setq host (pop command-line-args-left)))
              ((equal arg "--port")
               (setq port (string-to-number (or (pop command-line-args-left) ""))))
              ((file-directory-p arg)
               (setq files (append files (directory-files arg t "\\.json\\'"))))
              (t (setq files (append files (list (expand-file-name arg))))))))
    (unless files
      (princ "usage: emacs --batch -l tetris-mit.el -f tetris-mit-kav-batch \
[--host H --port P] TRACE.json|DIR ...\n")
      (kill-emacs 2))
    (let* ((tetris-mit-kav-print t)
           (results (condition-case err
                        (tetris-mit-kav-replay files host port)
                      (error (princ (format "tetris-mit-kav-batch: %s\n"
                                            (error-message-string err)))
                             (kill-emacs 2))))
           (summary (tetris-mit--kav-summary results)))
      (princ (concat summary "\n"))
      (kill-emacs (if (string-prefix-p "PASS" summary) 0 1)))))

;;;; Local play: tetris.el on the facade (NOT SPEC v1)

(defconst tetris-mit-local-buffer-name "*Tetris MIT local*"
  "Buffer of `tetris-mit-local'.")

(defconst tetris-mit-local-shape-codes ["O" "J" "L" "Z" "S" "T" "I"]
  "SPEC piece code for each index of `tetris-shapes', 0 to 6.
Read off the block coordinates in tetris.el: shape 0 is the 2x2
square, 1 is ###/..#, 2 is ###/#.., 3 is ##./.##, 4 is .##/##.,
5 is .#./### and 6 is the 4x1 bar.")

(defun tetris-mit-local-x-colors ()
  "The SPEC colors of the tetris.el shapes, as `tetris-x-colors' (0..1)."
  (vconcat (mapcar (lambda (code)
                     (vconcat (mapcar (lambda (v) (/ v 255.0))
                                      (tetris-mit-palette-rgb code))))
                   tetris-mit-local-shape-codes)))

(defun tetris-mit-local-tty-colors ()
  "The SPEC colors of the tetris.el shapes, as `tetris-tty-colors' (#rrggbb)."
  (vconcat (mapcar (lambda (code)
                     (apply #'format "#%02x%02x%02x" (tetris-mit-palette-rgb code)))
                   tetris-mit-local-shape-codes)))

(defun tetris-mit--local-setup ()
  "Resize and recolor tetris.el for the facade, in the current buffer only.
`tetris-width' and `tetris-height' are ordinary global options, so
they are made buffer-local here.  tetris.el computes `tetris-next-x'
and `tetris-score-x' once, when it is loaded, from the global width, so
those are recomputed.  `tetris-display-options' reads the colors when
gamegrid is initialized, so gamegrid is initialized again."
  (setq-local tetris-width tetris-mit-cols)
  (setq-local tetris-height tetris-mit-rows)
  (setq-local tetris-next-x (+ (* 2 tetris-top-left-x) tetris-width))
  (setq-local tetris-next-y tetris-top-left-y)
  (setq-local tetris-score-x tetris-next-x)
  (setq-local tetris-score-y (+ tetris-next-y 6))
  (setq-local tetris-buffer-width (max tetris-buffer-width (+ tetris-score-x 14)))
  (setq-local tetris-buffer-height
              (max tetris-buffer-height (+ tetris-top-left-y tetris-height 1)))
  (setq-local tetris-x-colors (tetris-mit-local-x-colors))
  (setq-local tetris-tty-colors (tetris-mit-local-tty-colors))
  (gamegrid-init (tetris-display-options)))

(defun tetris-mit--local-cell-rgb (cell)
  "The SPEC color, as [R G B], of the tetris.el board CELL."
  (if (and (integerp cell) (<= 0 cell 6))
      (apply #'vector (tetris-mit-palette-rgb
                       (aref tetris-mit-local-shape-codes cell)))
    (vector 0 0 0)))

(defun tetris-mit-local-frame ()
  "The tetris.el playfield of the current buffer, as a 17x9 frame.
The frame is a vector of rows of [R G B] cells in the SPEC palette.
Call this in a `tetris-mit-local' buffer."
  (let ((rows (make-vector tetris-mit-rows nil)))
    (dotimes (y tetris-mit-rows)
      (let ((row (make-vector tetris-mit-cols nil)))
        (dotimes (x tetris-mit-cols)
          (aset row x (tetris-mit--local-cell-rgb
                       (gamegrid-get-cell (+ tetris-top-left-x x)
                                          (+ tetris-top-left-y y)))))
        (aset rows y row)))
    rows))

(defvar-local tetris-mit--mirror-process nil "Connection to the display server.")
(defvar-local tetris-mit--mirror-server nil "The display server's hello.")
(defvar-local tetris-mit--mirror-timer nil "Pending frame flush.")
(defvar-local tetris-mit--mirror-frame-no 0 "Number of frames mirrored.")
(defvar-local tetris-mit--mirror-digest nil "Digest of the last frame mirrored.")
(defvar-local tetris-mit--mirror-state nil "Last (score lines) mirrored.")
(defvar-local tetris-mit--mirror-errors nil "Errors from the display server.")

;;;###autoload
(defun tetris-mit-local (&optional mirror)
  "Play Emacs's `tetris' on a 9-wide, 17-tall board, as on the facade.
This is tetris.el's game with the board resized and recolored.  It is
NOT SPEC v1.  All of the following are tetris.el's:
- rotation: its own tables, with no wall kicks;
- scoring: points per piece placed, from `tetris-shape-scores', and
  nothing for clearing lines;
- speed: `tetris-update-speed-function';
- piece selection: `tetris-allow-repetitions';
- no hold, ghost piece, hard-drop latch, line-clear flash or game-over
  animation.
For the SPEC game, use `tetris-mit-remote'.

The board size comes from tetris.el's options `tetris-width' and
`tetris-height', which are set to 9 and 17 in this buffer only.  The
pieces use the SPEC §4.1 palette, through `tetris-x-colors' and
`tetris-tty-colors'.

With a prefix argument MIRROR, or when `tetris-mit-mirror' is non-nil,
every board update is also sent as a protocol frame to the display
server at `tetris-mit-display-host' and `tetris-mit-display-port', at
most 30 per second.  This lets tetris.el drive the facade simulator,
or the building.  The mirrored state has tetris.el's score, its row
count as lines, and level 0, because tetris.el has no levels.

The keys are tetris.el's: \\<tetris-mode-map>\\[tetris-move-left] and \\[tetris-move-right] move, \\[tetris-rotate-prev] rotates,
\\[tetris-move-down] moves down, \\[tetris-move-bottom] drops, \\[tetris-pause-game] pauses, \\[tetris-start-game] starts a new game, and
\\[tetris-end-game] ends the game."
  (interactive "P")
  (let ((buffer (get-buffer-create tetris-mit-local-buffer-name)))
    (select-window (or (get-buffer-window buffer) (selected-window)))
    (switch-to-buffer buffer)
    (tetris-mit-local-mirror-stop)
    (gamegrid-kill-timer)
    (tetris-mode)                       ; kills local variables
    (tetris-mit--local-setup)
    (add-hook 'kill-buffer-hook #'tetris-mit-local-mirror-stop nil t)
    (when (or mirror tetris-mit-mirror)
      (tetris-mit-local-mirror-start))
    (tetris-start-game)
    buffer))

(defun tetris-mit-local-mirror-start (&optional host port)
  "Mirror this `tetris-mit-local' board to the display server at HOST:PORT.
They default to `tetris-mit-display-host' and `tetris-mit-display-port'."
  (interactive
   (list (read-string "Display host: " tetris-mit-display-host)
         (read-number "Display port: " tetris-mit-display-port)))
  (unless (derived-mode-p 'tetris-mode)
    (user-error "Not a tetris-mit-local buffer"))
  (tetris-mit-local-mirror-stop)
  (let ((buffer (current-buffer)))
    (setq tetris-mit--mirror-frame-no 0
          tetris-mit--mirror-digest nil
          tetris-mit--mirror-state nil
          tetris-mit--mirror-errors nil
          tetris-mit--mirror-server nil)
    (setq tetris-mit--mirror-process
          (let ((target (tetris-mit-resolve-endpoint
                         (or host tetris-mit-display-host)
                         (or port tetris-mit-display-port) "producer")))
            (tetris-mit-open "tetris-mit-mirror" (car target) (cdr target)
                           "producer"
                           (lambda (proc msg)
                             (when (buffer-live-p buffer)
                               (with-current-buffer buffer
                                 (when (eq proc tetris-mit--mirror-process)
                                   (tetris-mit--mirror-receive msg))))))))
    (advice-add 'gamegrid-set-cell :after #'tetris-mit--mirror-after-set-cell)
    (tetris-mit--mirror-schedule)))

(defun tetris-mit--mirror-receive (msg)
  "Handle MSG from the display server."
  (pcase (alist-get 'type msg)
    ("hello"
     (setq tetris-mit--mirror-server msg)
     (cond ((not (equal (alist-get 'mode msg) "display"))
            (message "tetris-mit: the mirror target is in %s mode, not display"
                     (alist-get 'mode msg)))
           ;; Contract v1 (§2): only a producer may send frames.
           ((not (member (alist-get 'client_role msg) '(nil "producer")))
            (message "tetris-mit: the display server admitted the mirror as a %s; \
not mirroring" (alist-get 'client_role msg))
            (tetris-mit-local-mirror-stop))))
    ("error"
     (push msg tetris-mit--mirror-errors)
     (message "tetris-mit mirror: %s: %s" (alist-get 'code msg)
              (alist-get 'message msg)))
    ("closed"
     (setq tetris-mit--mirror-process nil)
     (tetris-mit--mirror-cancel-timer)
     (message "tetris-mit: mirror disconnected"))))

(defun tetris-mit--mirror-after-set-cell (&rest _)
  "After a gamegrid cell changes, schedule a mirrored frame."
  (when tetris-mit--mirror-process
    (tetris-mit--mirror-schedule)))

(defun tetris-mit--mirror-schedule ()
  "Send the board within 1/30 s, coalescing the updates in between."
  (unless tetris-mit--mirror-timer
    (setq tetris-mit--mirror-timer
          (run-with-timer (/ 1.0 tetris-mit-fps) nil
                          #'tetris-mit--mirror-flush (current-buffer)))))

(defun tetris-mit--mirror-cancel-timer ()
  "Cancel a pending mirrored frame."
  (when (timerp tetris-mit--mirror-timer)
    (cancel-timer tetris-mit--mirror-timer))
  (setq tetris-mit--mirror-timer nil))

(defun tetris-mit--mirror-flush (buffer)
  "Send BUFFER's board to its display server."
  (when (buffer-live-p buffer)
    (with-current-buffer buffer
      (setq tetris-mit--mirror-timer nil)
      (tetris-mit-local-mirror-send))))

(defun tetris-mit-local-mirror-send ()
  "Send the board as a frame, and the score as a state, if they changed.
Return non-nil if anything was sent."
  (when (process-live-p tetris-mit--mirror-process)
    (let* ((rows (tetris-mit-local-frame))
           (digest (tetris-mit-frame-digest rows))
           (state (list tetris-score tetris-n-rows))
           msgs)
      (unless (equal state tetris-mit--mirror-state)
        (setq tetris-mit--mirror-state state)
        (push (tetris-mit-make-state tetris-score 0 tetris-n-rows) msgs))
      (unless (equal digest tetris-mit--mirror-digest)
        (setq tetris-mit--mirror-digest digest)
        (push `((type . "frame") (frame_no . ,tetris-mit--mirror-frame-no)
                (rows . ,rows) (digest . ,digest))
              msgs)
        (cl-incf tetris-mit--mirror-frame-no))
      (when msgs
        (apply #'tetris-mit-send tetris-mit--mirror-process (nreverse msgs))
        t))))

(defun tetris-mit-local-mirror-stop ()
  "Stop mirroring this board, after sending any pending frame."
  (interactive)
  (when tetris-mit--mirror-timer
    (tetris-mit--mirror-cancel-timer)
    (ignore-errors (tetris-mit-local-mirror-send)))
  (let ((proc tetris-mit--mirror-process))
    (setq tetris-mit--mirror-process nil)
    (when (process-live-p proc)
      (delete-process proc)))
  (unless (cl-some (lambda (b) (buffer-local-value 'tetris-mit--mirror-process b))
                   (buffer-list))
    (advice-remove 'gamegrid-set-cell #'tetris-mit--mirror-after-set-cell)))

(provide 'tetris-mit)

;;; tetris-mit.el ends here
