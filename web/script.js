const runButton = document.querySelector("#runButton");
const progressFill = document.querySelector("#progressFill");
const percentLabel = document.querySelector("#percentLabel");
const phaseLabel = document.querySelector("#phaseLabel");

const phases = [
  "Phase A: pre-scan",
  "Phase B: snapshot list",
  "Phase C: processing",
  "Organize-by-year"
];

runButton?.addEventListener("click", () => {
  let progress = 12;
  let phase = 0;
  runButton.textContent = "Scanning...";
  progressFill.style.width = `${progress}%`;
  percentLabel.textContent = `${progress}%`;
  phaseLabel.textContent = phases[phase];

  const timer = window.setInterval(() => {
    progress = Math.min(progress + Math.floor(Math.random() * 9) + 6, 100);
    phase = Math.min(Math.floor(progress / 28), phases.length - 1);
    progressFill.style.width = `${progress}%`;
    percentLabel.textContent = `${progress}%`;
    phaseLabel.textContent = phases[phase];

    if (progress === 100) {
      window.clearInterval(timer);
      runButton.textContent = "Dry run complete";
      window.setTimeout(() => {
        runButton.textContent = "Run dry scan";
      }, 1800);
    }
  }, 360);
});
