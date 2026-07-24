const lifecycle = {
  doctor: ["blendersessiond doctor", "ready to host a Session"],
  start: ["blendersessiond start --name demo", 'Session "demo" healthy on 127.0.0.1:51842'],
  call: ["blendersessiond call get_scene_info --name demo", 'scene "(Unsaved)" · 3 objects · active Cube'],
  save: [
    `blendersessiond call execute_code --name demo --params '{"code":"bpy.ops.wm.save_mainfile()"}'`,
    "scene saved explicitly",
  ],
  stop: ["blendersessiond stop --name demo", "owned process tree stopped · logs retained"],
};

const stepButtons = document.querySelectorAll("[data-step]");
const commandOutput = document.querySelector("[data-console-command]");
const resultOutput = document.querySelector("[data-console-output]");
const lifecycleSection = document.querySelector("#how-it-works");
const reduceMotion = window.matchMedia("(prefers-reduced-motion: reduce)");
let lifecycleIndex = 0;
let lifecycleTimer;
let lifecycleIsVisible = false;

function activateLifecycleStep(button, announce = false) {
  const result = lifecycle[button.dataset.step];
  if (!result) return;

  for (const candidate of stepButtons) {
    const selected = candidate === button;
    candidate.classList.toggle("is-active", selected);
    candidate.setAttribute("aria-pressed", String(selected));
  }

  if (announce) {
    resultOutput.parentElement.setAttribute("aria-live", "polite");
  }
  [commandOutput.textContent, resultOutput.textContent] = result;
  if (announce) {
    window.setTimeout(() => resultOutput.parentElement.setAttribute("aria-live", "off"), 600);
  }
}

function scheduleLifecycle(delay = 2400) {
  window.clearTimeout(lifecycleTimer);
  if (!lifecycleIsVisible || reduceMotion.matches || stepButtons.length === 0) return;

  lifecycleTimer = window.setTimeout(() => {
    lifecycleIndex = (lifecycleIndex + 1) % stepButtons.length;
    activateLifecycleStep(stepButtons[lifecycleIndex]);
    scheduleLifecycle();
  }, delay);
}

for (const button of stepButtons) {
  button.addEventListener("click", () => {
    lifecycleIndex = [...stepButtons].indexOf(button);
    activateLifecycleStep(button, true);
    scheduleLifecycle(3600);
  });
}

const terminal = document.querySelector(".terminal");
const mcpDiagram = document.querySelector(".mcp-diagram");
const mcpStages = [
  "show-agent",
  "show-agent-link",
  "show-server",
  "show-server-link",
  "show-branches",
  "show-session-1",
  "show-session-2",
  "show-session-3",
];
let mcpTimer;
let mcpStage = 0;
let mcpIsVisible = false;

function resetMcpSequence() {
  window.clearTimeout(mcpTimer);
  mcpDiagram?.classList.remove(...mcpStages);
  mcpStage = 0;
}

function runMcpSequence() {
  window.clearTimeout(mcpTimer);
  if (!mcpDiagram || !mcpIsVisible || reduceMotion.matches) return;

  if (mcpStage === 0) {
    mcpDiagram.classList.remove(...mcpStages);
  }

  mcpDiagram.classList.add(mcpStages[mcpStage]);
  mcpStage += 1;

  if (mcpStage < mcpStages.length) {
    mcpTimer = window.setTimeout(runMcpSequence, 620);
  } else {
    mcpStage = 0;
    mcpTimer = window.setTimeout(runMcpSequence, 2200);
  }
}

if (!reduceMotion.matches) {
  mcpDiagram?.classList.add("is-sequenced");
}

const motionObserver = new IntersectionObserver(
  (entries) => {
    for (const entry of entries) {
      if (entry.target === terminal && entry.isIntersecting) {
        terminal.classList.add("is-animated");
        motionObserver.unobserve(terminal);
      }

      if (entry.target === lifecycleSection) {
        lifecycleIsVisible = entry.isIntersecting;
        if (lifecycleIsVisible) scheduleLifecycle(1800);
        else window.clearTimeout(lifecycleTimer);
      }

      if (entry.target === mcpDiagram) {
        mcpIsVisible = entry.isIntersecting;
        if (mcpIsVisible) {
          resetMcpSequence();
          mcpTimer = window.setTimeout(runMcpSequence, 250);
        } else {
          resetMcpSequence();
        }
      }
    }
  },
  { threshold: 0.28 },
);

if (terminal) motionObserver.observe(terminal);
if (lifecycleSection) motionObserver.observe(lifecycleSection);
if (mcpDiagram) motionObserver.observe(mcpDiagram);

const copyButton = document.querySelector("[data-copy-command]");
const copyStatus = document.querySelector("[data-copy-status]");
const quickstartCommands = `git clone https://github.com/BramVR/blendersessiond.git
uv tool install ./blendersessiond
blendersessiond doctor
blendersessiond start --name demo
blendersessiond call get_scene_info --name demo`;

function reportCopy(message, buttonLabel) {
  copyStatus.textContent = message;
  copyButton.querySelector("b").textContent = buttonLabel;
}

function legacyCopy(text) {
  const textarea = document.createElement("textarea");
  textarea.value = text;
  textarea.setAttribute("readonly", "");
  textarea.style.position = "fixed";
  textarea.style.opacity = "0";
  document.body.append(textarea);
  textarea.select();
  const copied = document.execCommand("copy");
  textarea.remove();
  return copied;
}

copyButton?.addEventListener("click", async () => {
  try {
    if (!navigator.clipboard) throw new Error("Clipboard API unavailable");
    await navigator.clipboard.writeText(quickstartCommands);
    reportCopy("Quickstart commands copied to clipboard.", "Copied");
  } catch {
    if (legacyCopy(quickstartCommands)) {
      reportCopy("Quickstart commands copied to clipboard.", "Copied");
    } else {
      window.prompt("Copy the quickstart commands:", quickstartCommands);
      reportCopy("Clipboard access unavailable. Commands opened for manual copying.", "Copy manually");
    }
  }

  window.setTimeout(() => {
    copyButton.querySelector("b").textContent = "Copy";
    copyStatus.textContent = "";
  }, 2000);
});
