// Consumers can subscribe on any input page before the user submits a take.
export function publishInputEvent(event) {
  window.dispatchEvent(new CustomEvent('jammate:input', {detail: structuredClone(event)}));
}
