(() => {
  'use strict';
  class ObserverRoutePlayback {
    constructor({onChange, dimMs = 180, holdMs = 100}) {
      this.onChange = onChange; this.dimMs = dimMs; this.holdMs = holdMs;
      this.current = null; this.queue = []; this.seen = new Set();
      this.runKey = null; this.live = false; this.motion = true;
      this.timer = null; this.finished = false; this.notifying = false; this.destroyed = false;
      this.paused = false; this.timerTask = null; this.timerRemaining = 0; this.timerDeadline = 0;
    }
    sync({runKey, stages, live, motion = true}) {
      if (this.destroyed) return;
      const newRun = runKey !== this.runKey, reset = newRun || live !== this.live;
      this.motion = motion;
      if (!reset && !stages.length && !this.current && !this.queue.length) return;
      if (reset || !live || !stages.length) {
        this.clearTimer();
        this.runKey = runKey; this.live = live; this.queue = [];
        this.seen = new Set(stages.map(stage => stage.key));
        const stage = stages.at(-1);
        if (!reset && !live && this.current?.stage.key === stage?.key && !this.current?.dimming) return;
        this.current = stage ? {stage, animate: live && motion && newRun && stages.length === 1, dimming: false} : null;
        this.finished = false; this.notify(); return;
      }
      for (const stage of stages) if (!this.seen.has(stage.key)) {
        this.seen.add(stage.key); this.queue.push(stage);
      }
      if (!this.current && this.queue.length) this.startNext();
      else if (this.current && !motion && this.current.animate) {
        this.current.animate = false; this.notify();
      }
      this.advanceWhenReady();
    }
    finish(key) {
      if (!this.current || this.current.stage.key !== key || this.finished) return;
      this.finished = true; this.advanceWhenReady();
    }
    startNext() {
      const stage = this.queue.shift();
      this.current = stage ? {stage, animate: this.motion, dimming: false} : null;
      this.finished = false; this.notify();
    }
    advanceWhenReady() {
      if (!this.live || !this.current || !this.finished || this.timerTask || this.current.dimming) return;
      const stage = this.current.stage;
      const completed = stage.phase === 'output' || stage.atomic || stage.kind === 'result';
      if (!this.queue.length && !completed) return;
      const key = stage.key, hold = this.motion && completed ? this.holdMs : 0;
      this.schedule(() => {
        if (!this.current || this.current.stage.key !== key) return;
        this.current.dimming = true; this.notify();
        this.schedule(() => {
          if (!this.current || this.current.stage.key !== key) return;
          this.startNext();
        }, this.motion ? this.dimMs : 0);
      }, hold);
    }
    clearTimer() {
      clearTimeout(this.timer); this.timer = null; this.timerTask = null;
    }
    schedule(task, delay) {
      this.timerTask = task; this.timerRemaining = delay;
      if (this.paused) return;
      this.timerDeadline = performance.now() + delay;
      this.timer = setTimeout(() => {
        this.timer = null; this.timerTask = null; task();
      }, delay);
    }
    setPaused(paused) {
      if (this.destroyed || this.paused === paused) return;
      this.paused = paused;
      if (paused && this.timer !== null) {
        this.timerRemaining = Math.max(0, this.timerDeadline - performance.now());
        clearTimeout(this.timer); this.timer = null;
      } else if (!paused && this.timerTask) {
        this.schedule(this.timerTask, this.timerRemaining);
      }
    }
    notify() {
      if (this.notifying || this.destroyed) return;
      this.notifying = true;
      queueMicrotask(() => {
        this.notifying = false;
        if (!this.destroyed) this.onChange();
      });
    }
    destroy() {
      this.destroyed = true; this.clearTimer();
      this.queue = []; this.current = null;
    }
  }
  window.ObserverRoutePlayback = ObserverRoutePlayback;
})();
