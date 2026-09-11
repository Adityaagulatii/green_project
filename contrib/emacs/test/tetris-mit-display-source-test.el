;;; tetris-mit-display-source-test.el --- ERT for tetris-mit-display-source.el  -*- lexical-binding: t; -*-

;;; Commentary:

;; Framing, lease state and pacing, tested with a fake WebSocket link.
;; The local mock relay (contrib/displays) has not landed yet.  Nothing
;; here connects anywhere.
;;
;;   emacs --batch -Q -L contrib/emacs -L contrib/emacs/test -l ert \
;;     -l tetris-mit-display-source-test -f ert-run-tests-batch-and-exit

;;; Code:

(require 'ert)
(require 'cl-lib)
(require 'tetris-mit-display)
(require 'tetris-mit-display-source)
(require 'tetris-mit-test)

(defun tetris-mit-dsrc-test--fake ()
  "A fake link, and a function that returns what was sent, oldest first.
Each sent item is (text STRING), (binary BYTES) or (close)."
  (let* ((sent nil)
         (link (tetris-mit-display-source-make-link
                :send-text (lambda (s) (push (list 'text s) sent))
                :send-binary (lambda (b) (push (list 'binary b) sent))
                :close (lambda () (push (list 'close) sent)))))
    (cons link (lambda () (reverse sent)))))

(defun tetris-mit-dsrc-test--texts (log)
  "The decoded JSON text messages in LOG."
  (cl-loop for item in log when (eq (car item) 'text)
           collect (tetris-mit--json-parse (cadr item))))

(defun tetris-mit-dsrc-test--binaries (log)
  "The binary messages in LOG."
  (cl-loop for item in log when (eq (car item) 'binary) collect (cadr item)))

(defun tetris-mit-dsrc-test--granted (w h &optional fps)
  "A `granted' message for a W x H display at FPS."
  (json-encode `((op . "granted") (lease . "L1") (w . ,w) (h . ,h)
                 (fps . ,(or fps 30)) (expires . 1789000000))))

(defmacro tetris-mit-dsrc-test--with-source (spec &rest body)
  "SPEC is (SOURCE LOG DISPLAY &rest OPEN-ARGS): open a faked source."
  (declare (indent 1))
  (let ((fake (make-symbol "fake")))
    `(let* ((,fake (tetris-mit-dsrc-test--fake))
            (,(car spec) (tetris-mit-display-source-open
                          ,(nth 2 spec) :link (car ,fake) :name "emacs@test"
                          ,@(nthcdr 3 spec)))
            (,(cadr spec) (cdr ,fake)))
       (unwind-protect (progn ,@body)
         (tetris-mit-display-source-stop ,(car spec))))))

(ert-deftest tetris-mit-dsrc-test-reserve-message ()
  (tetris-mit-dsrc-test--with-source (source log "trs80" :ttl 5000)
    (should (eq (tetris-mit-display-source-state source) 'reserving))
    (should (equal (car (tetris-mit-dsrc-test--texts (funcall log)))
                   '((op . "reserve") (name . "emacs@test") (display . "trs80")
                     (ttl . 900))))
    (should-not (tetris-mit-dsrc-test--binaries (funcall log))))
  (should (string-match-p "\\`[^@]+@[^@.]+\\'" (tetris-mit-display-source--default-name))))

(ert-deftest tetris-mit-dsrc-test-pack ()
  "Row-major RGB, row 0 at the top, column 0 at the left; centered, cropped."
  (let ((rows (tetris-mit-test--rows)))
    (tetris-mit-test--set rows 0 0 [255 0 0])
    (tetris-mit-test--set rows 16 8 [0 0 255])
    ;; green-building: exact, 9 x 17
    (let ((bytes (tetris-mit-display-source-pack rows 9 17)))
      (should (= (length bytes) (* 9 17 3)))
      (should-not (multibyte-string-p bytes))
      (should (equal (substring bytes 0 3) (unibyte-string 255 0 0)))
      (should (equal (substring bytes (- (length bytes) 3)) (unibyte-string 0 0 255)))
      (should (equal bytes (tetris-mit-frame-bytes rows))))
    ;; tetris: 10 x 20, centered (dx 0, dy 1)
    (let ((bytes (tetris-mit-display-source-pack rows 10 20)))
      (should (= (length bytes) 600))
      (should (equal (substring bytes 0 30) (make-string 30 0)))
      (should (equal (substring bytes 30 33) (unibyte-string 255 0 0)))
      (should (equal (substring bytes (* 3 (+ (* 17 10) 8)) (* 3 (+ (* 17 10) 9)))
                     (unibyte-string 0 0 255))))
    ;; trs80: 10 x 12 is shorter than 17, so rows 2..13 are kept (dy -2)
    (let ((bytes (tetris-mit-display-source-pack rows 10 12)))
      (should (= (length bytes) 360))
      (should-not (string-match-p (string 255) bytes)))))

(ert-deftest tetris-mit-dsrc-test-granted-frames-and-sequence ()
  (tetris-mit-dsrc-test--with-source (source log "green-building")
    (let ((rows (tetris-mit-test--rows [255 170 0])))
      (tetris-mit-display-source-offer source rows)   ; before the grant: dropped
      (should (= (tetris-mit-display-source-dropped source) 1))
      (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
      (should (eq (tetris-mit-display-source-state source) 'granted))
      (should (equal (list (tetris-mit-display-source-w source)
                           (tetris-mit-display-source-h source))
                     '(9 17)))
      (tetris-mit-display-source-offer source rows)
      (let ((frames (tetris-mit-dsrc-test--binaries (funcall log))))
        (should (= (length frames) 1))
        (should (equal (car frames) (tetris-mit-frame-bytes rows))))
      (let ((tetris-mit-display-source-sequence t))
        (setf (tetris-mit-display-source-last-sent source) 0.0
              (tetris-mit-display-source-seq source) #xfffe)
        (tetris-mit-display-source-offer source rows)
        (setf (tetris-mit-display-source-last-sent source) 0.0)
        (tetris-mit-display-source-offer source rows)
        (setf (tetris-mit-display-source-last-sent source) 0.0)
        (tetris-mit-display-source-offer source rows))
      (let ((frames (cdr (tetris-mit-dsrc-test--binaries (funcall log)))))
        (should (equal (mapcar (lambda (b) (substring b 0 2)) frames)
                       (list (unibyte-string #xff #xfe) (unibyte-string #xff #xff)
                             (unibyte-string 0 0))))
        (should (= (length (car frames)) (+ 2 (* 9 17 3))))))))

(ert-deftest tetris-mit-dsrc-test-fps-drops-never-queues ()
  (tetris-mit-dsrc-test--with-source (source log "green-building")
    (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17 10))
    (let ((a (tetris-mit-test--rows [1 1 1]))
          (b (tetris-mit-test--rows [2 2 2]))
          (c (tetris-mit-test--rows [3 3 3])))
      (tetris-mit-display-source-offer source a)     ; sent now
      (tetris-mit-display-source-offer source b)     ; waits for the next slot
      (tetris-mit-display-source-offer source c)     ; replaces b
      (should (= (length (tetris-mit-dsrc-test--binaries (funcall log))) 1))
      (should (= (tetris-mit-display-source-dropped source) 1))
      (should (tetris-mit--wait-until
               (lambda () (= (length (tetris-mit-dsrc-test--binaries (funcall log))) 2))
               2))
      (should (equal (cadr (tetris-mit-dsrc-test--binaries (funcall log)))
                     (tetris-mit-frame-bytes c)))
      (should-not (tetris-mit-display-source-pending source))
      (sit-for 0.3)
      (should (= (length (tetris-mit-dsrc-test--binaries (funcall log))) 2)))))

(ert-deftest tetris-mit-dsrc-test-busy-and-not-holder ()
  (tetris-mit-dsrc-test--with-source (source log "green-building")
    (tetris-mit-display-source-receive
     source "{\"op\":\"busy\",\"holder\":\"someone@else\",\"expires\":1789000000}")
    (should (eq (tetris-mit-display-source-state source) 'busy))
    (should (equal (tetris-mit-display-source-holder source) "someone@else"))
    (tetris-mit-display-source-offer source (tetris-mit-test--rows))
    (should-not (tetris-mit-dsrc-test--binaries (funcall log))))
  (tetris-mit-dsrc-test--with-source (source log "green-building")
    (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
    (tetris-mit-display-source-receive source "{\"op\":\"error\",\"reason\":\"not holder\"}")
    (should (eq (tetris-mit-display-source-state source) 'lost))
    (tetris-mit-display-source-offer source (tetris-mit-test--rows))
    (should-not (tetris-mit-dsrc-test--binaries (funcall log)))
    (tetris-mit-display-source-receive source "not json at all")
    (should (eq (tetris-mit-display-source-state source) 'lost))))

(ert-deftest tetris-mit-dsrc-test-renew-when-idle-and-release ()
  (let ((tetris-mit-display-source-renew-interval 0.1))
    (tetris-mit-dsrc-test--with-source (source log "green-building")
      (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
      (should (tetris-mit--wait-until
               (lambda () (member '((op . "renew")) (tetris-mit-dsrc-test--texts (funcall log))))
               3))
      (tetris-mit-display-source-stop source)
      (should (eq (tetris-mit-display-source-state source) 'released))
      (let ((log (funcall log)))
        (should (equal (car (last (tetris-mit-dsrc-test--texts log))) '((op . "release"))))
        (should (equal (car (last log)) '(close))))
      (should-not (memq source tetris-mit-display-source--sources)))))

(ert-deftest tetris-mit-dsrc-test-stock-tetris-gamegrid ()
  "Stock tetris.el at 10 x 12 (d=trs80): the playing field goes out as RGB."
  (let ((fake (tetris-mit-dsrc-test--fake))
        (buffer (generate-new-buffer "*tetris-dsrc-test*")))
    (unwind-protect
        (with-current-buffer buffer
          (tetris-mode)
          (setq-local tetris-width 10)
          (setq-local tetris-height 12)
          (tetris-start-game)
          (gamegrid-kill-timer)
          (let ((source (tetris-mit-display-source-tetris "trs80" nil (car fake))))
            (should (equal (alist-get 'display (car (tetris-mit-dsrc-test--texts
                                                      (funcall (cdr fake)))))
                           "trs80"))
            (let ((rows (tetris-mit-display-source-gamegrid-rows)))
              (should (= (length rows) 12))
              (should (= (length (aref rows 0)) 10))
              (should (= 4 (cl-loop for row across rows
                                    sum (cl-count-if-not (lambda (c) (equal c [0 0 0])) row)))))
            (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 10 12))
            (let ((frames (tetris-mit-dsrc-test--binaries (funcall (cdr fake))))
                  (color (tetris-mit-display-source--cell-rgb tetris-shape)))
              (should (= (length frames) 1))  ; the field is sent on the grant
              (should (= (length (car frames)) 360))
              (should (equal (car frames)
                             (tetris-mit-display-source-pack
                              (tetris-mit-display-source-gamegrid-rows) 10 12)))
              (should (cl-search (apply #'unibyte-string (append color nil)) (car frames))))
            ;; a move changes cells; the advice offers the new field (paced)
            (tetris-move-left)
            (should (tetris-mit--wait-until
                     (lambda () (>= (length (tetris-mit-dsrc-test--binaries
                                             (funcall (cdr fake))))
                                    2))
                     2))
            (should (equal (car (last (tetris-mit-dsrc-test--binaries (funcall (cdr fake)))))
                           (tetris-mit-display-source-pack
                            (tetris-mit-display-source-gamegrid-rows) 10 12)))))
      (kill-buffer buffer))
    ;; kill-buffer released the lease
    (should (equal (car (last (tetris-mit-dsrc-test--texts (funcall (cdr fake)))))
                   '((op . "release"))))
    (should-not (advice-member-p #'tetris-mit-display-source--after-set-cell
                                 'gamegrid-set-cell))))

(ert-deftest tetris-mit-dsrc-test-game-frames-from-the-overlay-display ()
  (let ((fake (tetris-mit-dsrc-test--fake))
        (buffer (tetris-mit-display-buffer)))
    (unwind-protect
        (let ((source (tetris-mit-display-source-game "green-building" nil (car fake)))
              (rows (tetris-mit-test--rows [0 255 255])))
          (tetris-mit-display-source-receive source (tetris-mit-dsrc-test--granted 9 17))
          (setf (tetris-mit-display-source-last-sent source) 0.0)
          (tetris-mit-display-show rows 5 nil buffer)
          (should (equal (car (last (tetris-mit-dsrc-test--binaries (funcall (cdr fake)))))
                         (tetris-mit-frame-bytes rows)))
          (tetris-mit-display-source-stop source)
          (should-not (memq #'tetris-mit-display-source--game-frame
                            tetris-mit-display-frame-functions)))
      (kill-buffer buffer))))

(ert-deftest tetris-mit-dsrc-test-refuses-non-loopback-relays ()
  "Never reach a live relay by accident, such as wss://wal.sh."
  (cl-letf (((symbol-function 'websocket-open)
             (lambda (&rest _) (error "Must not connect"))))
    (should-error (tetris-mit-display-source-open
                   "green-building" :url "wss://wal.sh/tools/display/ws")
                  :type 'user-error)
    (should-error (tetris-mit-display-source--check-url "http://127.0.0.1/x")
                  :type 'user-error))
  (should-not (tetris-mit-display-source--check-url "ws://127.0.0.1:8080/tools/display/ws"))
  (unless (locate-library "websocket")
    (should-error (tetris-mit-display-source--websocket-link
                   "ws://127.0.0.1:1/tools/display/ws" nil)
                  :type 'user-error)))

(provide 'tetris-mit-display-source-test)

;;; tetris-mit-display-source-test.el ends here
