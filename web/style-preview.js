import {attachBandPanel} from './band-panel.js';

const style = document.getElementById('reference-style');
const select = attachBandPanel(document.getElementById('band-panel'), {endpoint: '/api/reference/style'});
const update = () => select({style: style.value});
style.addEventListener('change', update);
update();
