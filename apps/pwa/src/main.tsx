import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import "./styles.css";
import { currentScreen, readBootstrap } from "./api/bootstrap";
import { Nav } from "./components/Nav";
import { App } from "./App";

const bootstrap = readBootstrap();
const root = document.getElementById("root");
if (root) {
  createRoot(root).render(
    <StrictMode>
      <Nav current={currentScreen(bootstrap)} />
      <main>
        <App bootstrap={bootstrap} screen={currentScreen(bootstrap)} />
      </main>
    </StrictMode>,
  );
}
