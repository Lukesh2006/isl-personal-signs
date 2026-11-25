// Samples 3 and 4: record a short clip in the browser and send it to the server.
// Use in a page served over https:// or http://localhost (needed for the camera).

// Sample 3: record `ms` milliseconds of video from a camera stream.
async function recordClip(stream, ms = 2500) {
  const recorder = new MediaRecorder(stream, { mimeType: "video/webm" });
  const chunks = [];
  recorder.ondataavailable = (event) => event.data.size && chunks.push(event.data);
  const stopped = new Promise((resolve) => (recorder.onstop = resolve));
  recorder.start();
  await new Promise((resolve) => setTimeout(resolve, ms));
  recorder.stop();
  await stopped;
  return new Blob(chunks, { type: "video/webm" });
}

// Sample 4: send the clip to the server and read its answer.
async function recognize(clip, token) {
  const response = await fetch("/recognize", {
    method: "POST",
    headers: { Authorization: "Bearer " + token },
    body: clip,
  });
  if (!response.ok) throw new Error("Server answered " + response.status);
  return response.json(); // for example { decision: "accepted", suggestion: "hello" }
}

// Example use:
//   const stream = await navigator.mediaDevices.getUserMedia({ video: true });
//   const clip = await recordClip(stream);
//   const result = await recognize(clip, myLoginToken);
//   console.log(result.decision, result.suggestion);
