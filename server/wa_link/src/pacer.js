// Runs jobs strictly one after another, with at least `gapMs` between the end of one and the start of the next.
// A few seconds between sends is the rule for an unofficial number: it keeps bursts out of the WhatsApp traffic.
export class Pacer {
  constructor({ gapMs, now = () => Date.now(), sleep = (ms) => new Promise((r) => setTimeout(r, ms)) }) {
    this.gapMs = gapMs;
    this.now = now;
    this.sleep = sleep;
    this.lastEnd = -Infinity;
    this.chain = Promise.resolve();
  }

  run(job) {
    const result = this.chain.then(async () => {
      const wait = this.lastEnd + this.gapMs - this.now();
      if (wait > 0) await this.sleep(wait);
      try {
        return await job();
      } finally {
        this.lastEnd = this.now();
      }
    });
    this.chain = result.catch(() => {});
    return result;
  }
}
