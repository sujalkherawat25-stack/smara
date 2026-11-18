import React from "react";
import ReactDOM from "react-dom/client";
import App from "./NativeApp";
import ControlBar from "./ControlBar";
import { isControlBar } from "./controlBarView";
import { startLongTaskLog } from "./perfLog";

// The same bundle serves the main window and the small "Smara is controlling your PC" bar window.
const bar = isControlBar(window.location.hash);
if (bar) document.documentElement.classList.add("control-bar-window");
else startLongTaskLog();   // dev-only: active only when localStorage.smaraPerf is "1"
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    {bar ? <ControlBar /> : <App />}
  </React.StrictMode>,
);
