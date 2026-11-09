import React from "react";
import ReactDOM from "react-dom/client";
import App from "./NativeApp";
import { startLongTaskLog } from "./perfLog";

startLongTaskLog();   // dev-only: active only when localStorage.smaraPerf is "1"
ReactDOM.createRoot(document.getElementById("root")!).render(
  <React.StrictMode>
    <App />
  </React.StrictMode>,
);
