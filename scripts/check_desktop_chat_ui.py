"""Offline browser fault-injection checks of the production Desktop frontend.

The Tauri transport is explicitly simulated here. This is UI regression QA,
not a native-runtime or real-model acceptance result. No credentials are used.
"""
import argparse
from pathlib import Path

from playwright.sync_api import expect, sync_playwright


TRANSPORT_FIXTURE = r"""
(() => {
  const callbacks = new Map(); let next = 1; let listener;
  window.__chatFixture = { calls: [], result: {session: {status: 'created'}},
    emit(payload) { if (listener) callbacks.get(listener)?.({event: 'smara-chat-event', id: 1, payload}); } };
  window.__TAURI_EVENT_PLUGIN_INTERNALS__ = {unregisterListener() {}};
  window.__TAURI_INTERNALS__ = {
    transformCallback(callback) { const id = next++; callbacks.set(id, callback); return id; },
    unregisterCallback(id) { callbacks.delete(id); },
    async invoke(command, payload = {}) {
      if (command === 'plugin:event|listen') { listener = payload.handler; return 1; }
      if (command === 'plugin:app|version') return '0.1.6-ui-test';
      if (command === 'load_connection') return {runtime_mode: 'local', api_url: 'http://127.0.0.1',
        web_url: 'http://127.0.0.1', workspace: 'C:\\fixture\\workspace', model_profile: 'local:fixture',
        capabilities: [], allowed_roots: [], terminal_allowlist: [], browser_domains: [],
        auto_approve_safe: false, approval_mode: 'ask', paused: false, running: false};
      if (command === 'list_local_model_profiles') return [{id:'fixture',label:'Offline UI fixture',provider:'fixture',model:'fixture'}];
      if (command === 'get_runtime_session') return window.__chatFixture.result;
      if (command === 'cancel_runtime_session') return {session:{status:'cancelled',cancel_requested:true}};
      if (command === 'session_protocol') return {pending_approvals:[]};
      if (command === 'stream_chat') return new Promise((resolve,reject) =>
        window.__chatFixture.calls.push({args:payload.args,resolve,reject}));
      return [];
    }
  };
})();
"""


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--url", default="http://127.0.0.1:1421")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    with sync_playwright() as playwright:
        browser = playwright.chromium.launch()
        page = browser.new_page(viewport={"width": 1440, "height": 1000})
        errors = []
        page.on("pageerror", lambda error: errors.append(str(error)))
        page.clock.install()
        page.add_init_script(TRANSPORT_FIXTURE)
        page.goto(args.url)
        composer = page.locator("textarea.composer-dock-textarea")
        page.get_by_role("combobox", name="Task mode").select_option("deep")
        long_message = "Explain database history with verified sources. 数据库\n" * 2000
        composer.fill(long_message)
        composer.press("Enter")
        page.wait_for_function("window.__chatFixture.calls.length === 1")
        assert page.evaluate("window.__chatFixture.calls[0].args.message.length") == len(long_message.strip())
        page.evaluate("""() => {
          const args = window.__chatFixture.calls[0].args;
          window.__chatFixture.emit({type:'status',session_id:args.conversation_id,request_id:args.request_id,
            label:'Waiting for model response',text:'Controlled slow-provider UI scenario.'});
        }""")
        expect(page.get_by_role("status")).to_have_text("Waiting for model response")
        expect(page.get_by_role("region", name="Current run")).to_be_in_viewport()
        expect(page.get_by_text("Show full message", exact=False)).to_be_visible()
        assert page.locator("details.long-user-message .long-message-content").text_content() == long_message.strip()
        page.clock.fast_forward(31_000)
        expect(page.get_by_text("No new progress for", exact=False)).to_be_visible()
        page.screenshot(path=str(args.output / "desktop-research-wait.png"), full_page=True)
        page.set_viewport_size({"width": 1024, "height": 768})
        assert page.evaluate("document.documentElement.scrollWidth <= window.innerWidth")
        expect(page.get_by_role("region", name="Current run")).to_be_in_viewport()
        page.screenshot(path=str(args.output / "desktop-research-wait-compact.png"), full_page=True)

        page.get_by_title("Stop generating (Cancel)", exact=True).click()
        expect(page.get_by_role("region", name="Current run")).to_have_count(0)
        composer.fill("Second turn after cancellation")
        composer.press("Enter")
        page.wait_for_function("window.__chatFixture.calls.length === 2")
        expect(page.get_by_text("Cancellation requested.", exact=False)).to_have_count(0)
        page.evaluate("""() => {
          const old = window.__chatFixture.calls[0];
          window.__chatFixture.emit({type:'done',session_id:old.args.conversation_id,
            request_id:old.args.request_id,status:'completed',completed:true});
          old.reject(new Error('Late error from the cancelled worker'));
        }""")
        expect(page.get_by_role("region", name="Current run")).to_be_visible()
        page.evaluate("""() => {
          const current = window.__chatFixture.calls[1];
          window.__chatFixture.result = {session:{status:'completed',result:{answer:'Recovered offline fixture result.'}}};
          current.resolve();
        }""")
        expect(page.get_by_text("Recovered offline fixture result.", exact=True)).to_be_visible()
        expect(page.get_by_role("region", name="Current run")).to_have_count(0)

        composer.fill("Third turn: controlled provider failure")
        composer.press("Enter")
        page.wait_for_function("window.__chatFixture.calls.length === 3")
        page.evaluate("window.__chatFixture.calls[2].reject(new Error('Controlled provider timeout'))")
        expect(page.get_by_text("Controlled provider timeout", exact=True)).to_be_visible()
        expect(page.get_by_role("button", name="Retry this turn")).to_be_enabled()
        expect(page.get_by_role("region", name="Current run")).to_have_count(0)
        page.clock.fast_forward(1000)
        page.screenshot(path=str(args.output / "desktop-research-retry.png"), full_page=True)
        assert not errors, errors
        browser.close()
    print("PASS: long input, progress/quiet warning, compact layout, cancellation race, lost completion recovery, retryable failure")
    print("Scope: production frontend with offline transport fault injection; not real-model/native acceptance")


if __name__ == "__main__":
    main()
