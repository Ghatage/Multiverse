const prompt = document.getElementById("prompt");
const submit = document.getElementById("submit");
let submitting = false;
async function start() {
  if (submitting || !prompt.value.trim()) return;
  submitting = true;
  submit.disabled = true;
  try {
    await window.multiverse.action("submit-prompt", prompt.value);
  } catch (error) {
    submitting = false;
    submit.disabled = !prompt.value.trim();
    document.getElementById("hint").textContent = error.message;
  }
}
prompt.addEventListener("input", () => {
  submit.disabled = submitting || !prompt.value.trim();
});
prompt.addEventListener("keydown", (event) => {
  if (
    event.key === "Enter" &&
    !event.shiftKey &&
    !event.isComposing &&
    event.keyCode !== 229
  ) {
    event.preventDefault();
    start();
  }
});
document.addEventListener("keydown", (event) => {
  if (event.key === "Escape") window.multiverse.action("cancel-prompt");
});
submit.addEventListener("click", start);
window.addEventListener("focus", () => prompt.focus());
