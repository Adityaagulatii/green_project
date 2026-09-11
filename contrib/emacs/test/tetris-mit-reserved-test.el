;;; tetris-mit-reserved-test.el --- ERT for dlk1 lease keys and play-reserved  -*- lexical-binding: t; -*-

;;; Commentary:

;; Experiment 002, the Emacs part: the display source carries a dlk1
;; lease key (inputs/dlk1-display-lease-key.md), acts on the relay's
;; answers, and the user's scenario runs end to end.
;;
;; - The key in `reserve', from `:key', the option or the key file.
;; - The client honouring the key: its display, its formats, its end.
;; - `unauthorized': stop, show the detail, never retry.
;; - The key's `exp': stop sending, and say the slot ended; the boundary
;;   when exp is reached in the middle of the stream.
;; - End to end against the displays kit's mock relay (spec v0.2.1), when
;;   it is in the checkout: the scripted user plays KAV-07 on a reserved
;;   green-building display, in-process and through the batch command.
;;   The mock relay does not verify keys yet (no --lease-secret), so the
;;   key is carried but not checked there.
;;
;; Keys here are signed with the published test-only secret of kid
;; `test' (the bytes 0x00..0x1f); the signer is checked against Python's
;; hmac on one known answer.
;;
;;   emacs --batch -Q -L contrib/emacs -L contrib/emacs/test -l ert \
;;     -l tetris-mit-reserved-test -f ert-run-tests-batch-and-exit

;;; Code:

(require 'ert)
(require 'cl-lib)
(require 'tetris-mit-display-source)
(require 'tetris-mit-reserved)
(require 'tetris-mit-display-source-test)

;;;; A test-only dlk1 signer

(defconst tetris-mit-reserved-test--secret
  (apply #'unibyte-string (number-sequence 0 31))
  "The published, test-only secret of kid `test': the bytes 0x00..0x1f.")

(defun tetris-mit-reserved-test--hmac (key message)
  "HMAC-SHA256 of MESSAGE under KEY, both unibyte strings, as raw bytes."
  (let* ((key (if (> (length key) 64) (secure-hash 'sha256 key nil nil t) key))
         (key (concat key (make-string (- 64 (length key)) 0)))
         (pad (lambda (x) (apply #'unibyte-string (mapcar (lambda (b) (logxor b x)) key)))))
    (secure-hash 'sha256
                 (concat (funcall pad #x5c)
                         (secure-hash 'sha256 (concat (funcall pad #x36) message) nil nil t))
                 nil nil t)))

(defun tetris-mit-reserved-test--mint (claims &optional secret)
  "A dlk1 key for CLAIMS (an alist), signed with SECRET (the test secret)."
  (let* ((sorted (sort (copy-sequence claims)
                       (lambda (a b) (string< (symbol-name (car a)) (symbol-name (car b))))))
         (payload (base64url-encode-string
                   (encode-coding-string (let ((json-encoding-pretty-print nil))
                                           (json-encode sorted))
                                         'utf-8)
                   t))
         (signature (base64url-encode-string
                     (tetris-mit-reserved-test--hmac
                      (or secret tetris-mit-reserved-test--secret)
                      (encode-coding-string (concat "dlk1." payload) 'utf-8))
                     t)))
    (concat "dlk1." payload "." signature)))

(defun tetris-mit-reserved-test--claims (&rest overrides)
  "Valid claims for green-building, now and for 10 minutes, with OVERRIDES.
OVERRIDES is a plist such as (:exp 1789000000 :fmt [\"hex\"])."
  (let* ((now (floor (float-time)))
         (claims (list (cons 'v 1) (cons 'kid "test") (cons 'iss "dres")
                       (cons 'sub "emacs@ert") (cons 'rid "r-1")
                       (cons 'display "green-building") (cons 'nbf (- now 10))
                       (cons 'exp (+ now 600)) (cons 'fmt ["pal16" "hex"])
                       (cons 'jti "j-1"))))
    (cl-loop for (k v) on overrides by #'cddr
             do (setf (alist-get (intern (substring (symbol-name k) 1)) claims) v))
    claims))

(ert-deftest tetris-mit-reserved-test-signer-known-answer ()
  "The test signer agrees with Python's hmac and base64 on one key."
  (should (equal (tetris-mit-reserved-test--mint
                  (tetris-mit-reserved-test--claims :nbf 1789000000 :exp 1789000600))
                 (concat "dlk1.eyJkaXNwbGF5IjoiZ3JlZW4tYnVpbGRpbmciLCJleHAiOjE3ODkwMDA2MDAs"
                         "ImZtdCI6WyJwYWwxNiIsImhleCJdLCJpc3MiOiJkcmVzIiwianRpIjoiai0xIiwia2lk"
                         "IjoidGVzdCIsIm5iZiI6MTc4OTAwMDAwMCwicmlkIjoici0xIiwic3ViIjoiZW1hY3NA"
                         "ZXJ0IiwidiI6MX0.Vic1nHsXflLbzJfQg9h7e5qg5WC_agxFmXHscsDt184"))))

;;;; The key in reserve

(ert-deftest tetris-mit-reserved-test-key-in-reserve ()
  "The key goes in `reserve': from `:key', the option, or the key file."
  (let ((key (tetris-mit-reserved-test--mint (tetris-mit-reserved-test--claims))))
    (tetris-mit-dsrc-test--with-source (source log "green-building" :key key)
      (should (equal (car (tetris-mit-dsrc-test--texts (funcall log)))
                     `((op . "reserve") (name . "emacs@test") (display . "green-building")
                       (ttl . 300) (format . "pal16") (key . ,key))))
      (should (equal (alist-get 'sub (tetris-mit-display-source-claims source)) "emacs@ert"))
      (should (equal (alist-get 'fmt (tetris-mit-display-source-claims source))
                     ["pal16" "hex"])))
    (let ((tetris-mit-display-source-key key))
      (tetris-mit-dsrc-test--with-source (source log "green-building")
        (should (equal (alist-get 'key (car (tetris-mit-dsrc-test--texts (funcall log)))) key))))
    (let ((file (make-temp-file "dlk1-" nil ".key" (concat "  " key "\n"))))
      (unwind-protect
          (let ((tetris-mit-display-source-key-file file))
            (tetris-mit-dsrc-test--with-source (source log "green-building")
              (should (equal (alist-get 'key (car (tetris-mit-dsrc-test--texts (funcall log))))
                             key))))
        (delete-file file)))
    (tetris-mit-dsrc-test--with-source (source log "green-building")
      (should-not (assq 'key (car (tetris-mit-dsrc-test--texts (funcall log)))))
      (should-not (tetris-mit-display-source-claims source)))
    ;; a key that is not dlk1 is carried as it is, with no claims to honour
    (tetris-mit-dsrc-test--with-source (source log "green-building" :key "opaque-token")
      (should (equal (alist-get 'key (car (tetris-mit-dsrc-test--texts (funcall log))))
                     "opaque-token"))
      (should-not (tetris-mit-display-source-claims source)))))

(ert-deftest tetris-mit-reserved-test-client-honours-the-key ()
  "The client reserves what the key allows, and refuses a key it cannot use."
  (tetris-mit-dsrc-test--with-source
      (source log "green-building"
              :key (tetris-mit-reserved-test--mint (tetris-mit-reserved-test--claims :fmt ["hex"])))
    (should (equal (alist-get 'format (car (tetris-mit-dsrc-test--texts (funcall log)))) "hex"))
    (should (equal (tetris-mit-display-source-format source) "hex")))
  (let ((now (floor (float-time))))
    (pcase-dolist (`(,claims ,why)
                   `((,(tetris-mit-reserved-test--claims :display "tetris") "for display tetris")
                     (,(tetris-mit-reserved-test--claims :nbf (- now 100) :exp (- now 1)) "ended")
                     (,(tetris-mit-reserved-test--claims :fmt ["rgb24"]) "allows rgb24")))
      (let* ((fake (tetris-mit-dsrc-test--fake))
             (err (should-error (tetris-mit-display-source-open
                                 "green-building" :link (car fake) :name "x"
                                 :key (tetris-mit-reserved-test--mint claims))
                                :type 'user-error)))
        (should (string-match-p why (cadr err)))
        (should-not (funcall (cdr fake)))))))

;;;; The relay's answers

(defmacro tetris-mit-reserved-test--messages (var &rest body)
  "Run BODY with `message' collecting into VAR, newest first."
  (declare (indent 1))
  `(cl-letf (((symbol-function 'message)
              (lambda (fmt &rest args) (push (apply #'format fmt args) ,var) nil)))
     ,@body))

(ert-deftest tetris-mit-reserved-test-unauthorized-stops-without-retry ()
  "unauthorized: stop the source, show the detail, send no release and no retry."
  (dolist (detail '("missing" "malformed" "unknown-kid" "bad-signature" "bad-claims"
                    "wrong-display" "not-yet" "expired" "format-not-allowed"))
    (let ((messages nil))
      (tetris-mit-reserved-test--messages messages
        (tetris-mit-dsrc-test--with-source
            (source log "green-building"
                    :key (tetris-mit-reserved-test--mint (tetris-mit-reserved-test--claims)))
          (tetris-mit-display-source-receive
           source (format "{\"op\":\"error\",\"reason\":\"unauthorized\",\"detail\":\"%s\"}"
                          detail))
          (should (equal (list detail (tetris-mit-display-source-state source))
                         (list detail 'unauthorized)))
          (should (equal (tetris-mit-display-source-detail source) detail))
          (should (equal (tetris-mit-display-source-last-error source) "unauthorized"))
          (should-not (memq source tetris-mit-display-source--sources))
          (tetris-mit-display-source-offer source (tetris-mit-test--rows))
          (let ((log (funcall log)))
            (should (equal (car (last log)) '(close)))
            (should-not (tetris-mit-dsrc-test--binaries log))
            (should (equal (mapcar (lambda (m) (alist-get 'op m))
                                   (tetris-mit-dsrc-test--texts log))
                           '("reserve"))))))
      (should (cl-some (lambda (m)
                         (string-match-p (format "refused the lease key: %s (not retrying)"
                                                 detail)
                                         m))
                       messages)))))

(ert-deftest tetris-mit-reserved-test-slot-ends-at-exp ()
  "At the key's exp the source stops sending and says the slot ended.
The relay's not-holder that follows is the slot's end, not a lost lease."
  (let* ((now (float-time))
         (exp (+ (floor now) (if (< (- (ceiling now) now) 0.3) 2 1)))
         (messages nil))
    (tetris-mit-reserved-test--messages messages
      (tetris-mit-dsrc-test--with-source
          (source log "green-building"
                  :key (tetris-mit-reserved-test--mint
                        (tetris-mit-reserved-test--claims :exp exp)))
        (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
        (tetris-mit-display-source-offer source (tetris-mit-test--rows [255 0 0]))
        (should (= (length (tetris-mit-dsrc-test--binaries (funcall log))) 1))
        (should (tetris-mit--wait-until
                 (lambda () (eq (tetris-mit-display-source-state source) 'ended)) 4))
        (should (>= (float-time) exp))
        (setf (tetris-mit-display-source-last-sent source) 0.0)
        (tetris-mit-display-source-offer source (tetris-mit-test--rows [0 0 255]))
        (should (= (length (tetris-mit-dsrc-test--binaries (funcall log))) 1))
        (tetris-mit-display-source-receive source "{\"op\":\"error\",\"reason\":\"not-holder\"}")
        (should (eq (tetris-mit-display-source-state source) 'ended))
        (let ((ops (mapcar (lambda (m) (alist-get 'op m))
                           (tetris-mit-dsrc-test--texts (funcall log)))))
          (should (equal ops '("reserve"))))
        (should (equal (car (last (funcall log))) '(close)))))
    (should (cl-some (lambda (m) (string-match-p "reserved slot ended at .*; frames stopped" m))
                     messages))))

(ert-deftest tetris-mit-reserved-test-exp-reached-mid-stream ()
  "The boundary: a frame just before exp goes out; at exp nothing more does.
That holds for a frame offered at exp before the slot timer fires, and
for a frame still waiting for its fps slot when exp passes."
  (let* ((exp (+ (floor (float-time)) 600))
         (key (tetris-mit-reserved-test--mint (tetris-mit-reserved-test--claims :exp exp)))
         (clock (- exp 1.0)))
    (cl-letf (((symbol-function 'float-time) (lambda (&optional _) clock)))
      (tetris-mit-dsrc-test--with-source (source log "green-building" :key key)
        (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
        (setq clock (- exp 0.001))
        (tetris-mit-display-source-offer source (tetris-mit-test--rows [255 0 0]))
        (should (= (length (tetris-mit-dsrc-test--binaries (funcall log))) 1))
        (setq clock exp)
        (setf (tetris-mit-display-source-last-sent source) 0.0)
        (tetris-mit-display-source-offer source (tetris-mit-test--rows [0 0 255]))
        (should (= (length (tetris-mit-dsrc-test--binaries (funcall log))) 1))
        (should (eq (tetris-mit-display-source-state source) 'ended))
        (should (= (tetris-mit-display-source-dropped source) 1)))))
  ;; A waiting frame whose fps slot comes after exp is dropped at its slot.
  (let* ((exp (+ (floor (float-time)) 600))
         (key (tetris-mit-reserved-test--mint (tetris-mit-reserved-test--claims :exp exp))))
    (tetris-mit-dsrc-test--with-source (source log "green-building" :key key)
      (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17 10))
      (tetris-mit-display-source-offer source (tetris-mit-test--rows [255 0 0]))   ; sent
      (tetris-mit-display-source-offer source (tetris-mit-test--rows [0 0 255]))   ; waits
      (should (tetris-mit-display-source-pending source))
      (setf (alist-get 'exp (tetris-mit-display-source-claims source)) (floor (float-time)))
      (should (tetris-mit--wait-until
               (lambda () (eq (tetris-mit-display-source-state source) 'ended)) 2))
      (should (= (length (tetris-mit-dsrc-test--binaries (funcall log))) 1))
      (should-not (tetris-mit-display-source-pending source)))))

(ert-deftest tetris-mit-reserved-test-lease-and-not-holder-inside-the-slot ()
  "Inside the key's slot, a lost lease is `lost', not `ended'."
  (let ((key (tetris-mit-reserved-test--mint (tetris-mit-reserved-test--claims))))
    (tetris-mit-dsrc-test--with-source (source _log "green-building" :key key)
      (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
      (tetris-mit-display-source-receive source "{\"op\":\"error\",\"reason\":\"not-holder\"}")
      (should (eq (tetris-mit-display-source-state source) 'lost)))
    (tetris-mit-dsrc-test--with-source (source _log "green-building" :key key)
      (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
      (tetris-mit-display-source-receive
       source "{\"op\":\"lease\",\"display\":\"green-building\",\"holder\":null,\"expires\":null}")
      (should (eq (tetris-mit-display-source-state source) 'lost))))
  (let* ((exp (+ (floor (float-time)) 600))
         (key (tetris-mit-reserved-test--mint (tetris-mit-reserved-test--claims :exp exp)))
         (clock (- exp 5.0)))
    (cl-letf (((symbol-function 'float-time) (lambda (&optional _) clock)))
      (tetris-mit-dsrc-test--with-source (source _log "green-building" :key key)
        (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
        (setq clock (+ exp 0.5))
        (tetris-mit-display-source-receive
         source "{\"op\":\"lease\",\"display\":\"green-building\",\"holder\":null,\"expires\":null}")
        (should (eq (tetris-mit-display-source-state source) 'ended))))))

;;;; End to end: the user's scenario against the mock relay

(defmacro tetris-mit-reserved-test--with-relay (url &rest body)
  "Start the displays kit's mock relay on a free port, bind URL, run BODY."
  (declare (indent 1))
  `(tetris-mit-test--with-python
     (let* ((dir (tetris-mit-dsrc-test--relay-dir))
            (process-environment (cons "PYTHONDONTWRITEBYTECODE=1" process-environment))
            (buffer (generate-new-buffer " *mock-relay*"))
            (relay (let ((default-directory dir))
                     (make-process :name "mock-relay" :buffer buffer :noquery t
                                   :connection-type 'pipe
                                   :command (list tetris-mit-python "-m" "demo" "relay"
                                                  "--port" "0"))))
            (,url (tetris-mit--wait-until
                   (lambda ()
                     (with-current-buffer buffer
                       (save-excursion
                         (goto-char (point-min))
                         (and (re-search-forward "mock relay \\(ws://[^ \n]+\\)" nil t)
                              (match-string 1)))))
                   30)))
       (unwind-protect (progn (should ,url) ,@body)
         (when (process-live-p relay) (delete-process relay))
         (kill-buffer buffer)))))

(ert-deftest tetris-mit-reserved-test-play-on-the-mock-relay ()
  "The scripted user plays KAV-07 on a reserved green-building display.
It checks KAV-07's digests on the Emacs display, and that the frames a
viewer of the relay receives are displayed frames, in order."
  (tetris-mit-reserved-test--with-relay url
    (let ((result (tetris-mit-play-reserved-run
                   :relay url
                   :key (tetris-mit-reserved-test--mint (tetris-mit-reserved-test--claims))
                   :trace (tetris-mit-test--trace "07-hard-drop"))))
      (should (equal (plist-get result :verdict) "PASS"))
      (should (equal (plist-get result :digests) '(121 . 121)))
      (should (equal (plist-get result :granted) '(9 17 30 "pal16")))
      (should (> (plist-get result :relay-frames) 10))
      (should (plist-get result :relay-match))
      ;; Every frame sent is shown or refused; under host load the bridge
      ;; can bunch two frames, and the relay drops the early one (`rate').
      (should (= (+ (plist-get result :relay-frames) (plist-get result :refused))
                 (plist-get result :sent)))
      (should (<= (plist-get result :refused) (/ (plist-get result :sent) 10)))
      (should (eq (plist-get result :state) 'released)))))

(ert-deftest tetris-mit-reserved-test-batch-command ()
  "The harness's command: emacs --batch ... -f tetris-mit-play-reserved-batch."
  (tetris-mit-reserved-test--with-relay url
    (let ((file (make-temp-file "dlk1-" nil ".key"
                                (tetris-mit-reserved-test--mint
                                 (tetris-mit-reserved-test--claims)))))
      (unwind-protect
          (with-temp-buffer
            (let* ((process-environment (cons (concat "TETRIS_MIT_PYTHON=" tetris-mit-python)
                                              process-environment))
                   (status (call-process
                            (expand-file-name invocation-name invocation-directory)
                            nil '(t nil) nil "--batch" "-Q"
                            "-L" (expand-file-name "contrib/emacs" tetris-mit-root)
                            "-l" "tetris-mit-reserved" "-f" "tetris-mit-play-reserved-batch"
                            "--relay" url "--key-file" file)))
              (should (equal (list status (buffer-string))
                             (list 0 (buffer-string))))
              (should (string-match-p "^play-reserved: KAV-07: 121/121 digests match" (buffer-string)))
              (should (string-match-p "^PASS$" (buffer-string)))
              (should (string-match-p "^RESULT {\"id\":\"KAV-07\",\"verdict\":\"PASS\",\"exit\":0"
                                      (buffer-string)))))
        (delete-file file)))))

;;;; The harness's findings (requests reservation-main-20260911T1326Z/1339Z)

(ert-deftest tetris-mit-reserved-test-relay-display-option ()
  "The display option is --relay-display; Emacs itself takes --display."
  (should (equal (plist-get (tetris-mit-play-reserved--parse-args
                             '("--relay" "ws://127.0.0.1:1/tools/display/ws"
                               "--relay-display" "remote" "--key-file" "k.dlk1"))
                            :display)
                 "remote"))
  (should-error (tetris-mit-play-reserved--parse-args
                 '("--relay" "ws://127.0.0.1:1/tools/display/ws" "--display" "remote")))
  (should (string-match-p "unknown argument --display"
                          (cadr (should-error (tetris-mit-play-reserved--parse-args
                                               '("--display" "remote"))))))
  (should (string-prefix-p "usage" (cadr (should-error (tetris-mit-play-reserved--parse-args
                                                        '("--relay-display" "remote")))))))

(ert-deftest tetris-mit-reserved-test-relay-display-reaches-the-batch ()
  "Emacs passes --relay-display through: the entry point runs, and says usage.
Emacs moves a --display to the front of the arguments, so -Q is no longer
first and it exits with 255 before any function runs."
  (with-temp-buffer
    (let ((status (call-process
                   (expand-file-name invocation-name invocation-directory)
                   nil '(t t) nil "--batch" "-Q"
                   "-L" (expand-file-name "contrib/emacs" tetris-mit-root)
                   "-l" "tetris-mit-reserved" "-f" "tetris-mit-play-reserved-batch"
                   "--relay-display" "remote")))
      (should (equal (list status (buffer-string)) (list 2 (buffer-string))))
      (should (string-match-p "tetris-mit-play-reserved-batch: usage" (buffer-string)))
      (should-not (string-match-p "Unknown option" (buffer-string))))))

(ert-deftest tetris-mit-reserved-test-slot-end-takes-precedence ()
  "Frames are judged up to the relay's cut; the slot's end is exit 4.
At exp the relay announces a lease with holder null and sends a black
frame, which the source never showed: that mismatch is after the cut."
  (let* ((f1 (make-string 153 4)) (f2 (make-string 153 1)) (black (make-string 153 0))
         (log `((text ((op . "caps") (w . 9) (h . 17)))
                (text ((op . "lease") (holder . nil)))
                (text ((op . "lease") (holder . "emacs@ert")))
                (binary ,f1) (binary ,f2)
                (text ((op . "lease") (holder . nil)))
                (binary ,black)))
         (split (tetris-mit-reserved--frames-before-cut log))
         (shown (list f1 f2)))
    (should (equal split (cons (list f1 f2) (list black))))
    (should (tetris-mit-reserved--subsequence-p (car split) shown))
    (should-not (tetris-mit-reserved--subsequence-p (append (car split) (cdr split)) shown))
    ;; the verdict: unauthorized, then the slot's end, then the checks
    (should (= 4 (tetris-mit-reserved--verdict 'ended t nil nil)))
    (should (= 4 (tetris-mit-reserved--verdict 'ended nil nil nil)))
    (should (= 4 (tetris-mit-reserved--verdict 'ended t nil t)))
    (should (= 3 (tetris-mit-reserved--verdict 'unauthorized t nil t)))
    (should (= 0 (tetris-mit-reserved--verdict 'released t nil t)))
    (should (= 1 (tetris-mit-reserved--verdict 'released t nil nil)))
    (should (= 1 (tetris-mit-reserved--verdict 'released nil nil t)))
    (should (= 1 (tetris-mit-reserved--verdict 'lost t nil t)))))

(ert-deftest tetris-mit-reserved-test-slot-ends-mid-game-on-the-relay ()
  "The key's exp comes 3 s into KAV-12: the scripted user stops, exit 4.
The frames the relay got inside the slot are displayed frames, and the
digests match over the frames played."
  (tetris-mit-reserved-test--with-relay url
    (let* ((now (float-time))
           (exp (+ (ceiling now) (if (< (- (ceiling now) now) 0.5) 3 2)))
           (result (tetris-mit-play-reserved-run
                    :relay url :display "remote"
                    :key (tetris-mit-reserved-test--mint
                          (tetris-mit-reserved-test--claims :exp exp :display "remote"))
                    :trace (tetris-mit-test--trace "12-game-over-reset"))))
      (should (equal (list (plist-get result :verdict) (plist-get result :exit))
                     '("SLOT ENDED" 4)))
      (should (eq (plist-get result :state) 'ended))
      (should (< 20 (plist-get result :played) 447))
      (should (equal (car (plist-get result :digests)) (cdr (plist-get result :digests))))
      (should (plist-get result :relay-match))
      (should (= (plist-get result :frames) 447)))))

(ert-deftest tetris-mit-reserved-test-pacing-follows-the-granted-fps ()
  "Frames are (1 + margin)/fps apart, with the fps from `granted', not 30."
  (dolist (fps '(10 60))
    (let* ((times nil)
           (link (tetris-mit-display-source-make-link
                  :send-text #'ignore
                  :send-binary (lambda (_bytes) (push (float-time) times))
                  :close #'ignore))
           (source (tetris-mit-display-source-open "green-building" :link link :name "x")))
      (unwind-protect
          (progn
            (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17 fps))
            (should (= (tetris-mit-display-source-fps source) fps))
            (tetris-mit-display-source-offer source (tetris-mit-test--rows [255 0 0]))
            (tetris-mit-display-source-offer source (tetris-mit-test--rows [0 0 255]))
            (should (tetris-mit--wait-until (lambda () (= (length times) 2)) 2))
            (should (>= (- (car times) (cadr times))
                        (- (/ (+ 1.0 tetris-mit-display-source-pace-margin) fps) 0.002))))
        (tetris-mit-display-source-stop source)))))

(provide 'tetris-mit-reserved-test)

;;; tetris-mit-reserved-test.el ends here
