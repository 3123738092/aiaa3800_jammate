import {attachBandPanel} from './band-panel.js';

const prompt = document.getElementById('creation-prompt');
const selectInput = attachBandPanel(document.getElementById('band-panel'));

function updateInput() {
  selectInput(prompt.value.trim() ? {text: prompt.value} : null);
}

prompt.addEventListener('input', updateInput);
for (const button of document.querySelectorAll('[data-prompt]')) {
  button.addEventListener('click', () => {
    prompt.value = button.dataset.prompt;
    updateInput();
  });
}
updateInput();
