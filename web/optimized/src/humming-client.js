export function createHummingClient(url) {
  let worker;
  let nextId = 0;
  const pending = new Map();

  function request(type, {samples, ...input} = {}, progress) {
    return new Promise((resolve, reject) => {
      if (!worker) {
        worker = new Worker(url, {type: 'module'});
        worker.onmessage = ({data}) => {
          const job = pending.get(data.id);
          if (!job) return;
          if (data.progress !== undefined) {
            job.progress?.(data.progress);
            return;
          }
          pending.delete(data.id);
          if (data.error) job.reject(new Error(data.error));
          else job.resolve(data.document);
        };
        worker.onerror = () => {
          for (const job of pending.values()) job.reject(new Error('无法加载 Basic Pitch，请检查哼唱页面的构建文件。'));
          pending.clear();
          worker.terminate();
          worker = null;
        };
      }
      const id = ++nextId;
      const copy = samples?.slice();
      pending.set(id, {resolve, reject, progress});
      worker.postMessage({id, type, ...input, samples: copy}, copy ? [copy.buffer] : []);
    });
  }

  return {
    load: () => request('load'),
    transcribe: (input, progress) => request('transcribe', input, progress),
  };
}
