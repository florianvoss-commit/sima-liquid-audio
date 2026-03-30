// New Dashboard Elements
const chatMessages = document.getElementById('chatMessages');
const messageInput = document.getElementById('messageInput');
const sendButton = document.getElementById('sendButton');
const recordButton = document.getElementById('recordButton');
const recordIcon = document.getElementById('recordIcon');
const themeToggle = document.getElementById('themeToggle');
const themeIcon = document.getElementById('themeIcon');
const systemPromptButton = document.getElementById('systemPromptButton');
const systemPromptModal = document.getElementById('systemPromptModal');
const systemPromptTextarea = document.getElementById('systemPromptTextarea');
const systemPromptSave = document.getElementById('systemPromptSave');
const systemPromptCancel = document.getElementById('systemPromptCancel');
const systemPromptModalMessage = document.getElementById('systemPromptModalMessage');
const modeSelect = document.getElementById('modeSelect');
const voiceSelect = document.getElementById('voiceSelect');
const voiceRow = document.getElementById('voiceRow');

// Keep existing audio and recording variables
const outputAudio = document.getElementById('outputAudio');
const processingCanvas = document.getElementById('processingCanvas');

let isMicrophoneMuted = true;
let mediaStream = null;
let audioTracks = [];
let matrixInterval = null;
let recordedChunks = [];

const socket = io('/');
const audioQueue = [];
const decodedAudioQueue = [];
let isPlaying = false;
let currentAudioContext = null;
let animationId = null;
let receivedEndSignal = false;
let currentAudioElement = null;

let audioCtx = null;
let currentSource = null;
let currentAnalyser = null;
let currentAudioBuffer = null;
let shouldPlayAudio = true;
let responseAborted = false;
let currentSourceNode = null;
let decodeInFlight = false;
let scheduledSources = 0;
let nextPlaybackTime = 0;
let playbackStarted = false;
const AUDIO_PREROLL_CHUNKS = 1;

function b64ToUint8Array(b64) {
  const bin = atob(b64);
  const bytes = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i += 1) bytes[i] = bin.charCodeAt(i);
  return bytes;
}

function pcm16B64ToFloat32(b64) {
  const u8 = b64ToUint8Array(b64);
  const i16 = new Int16Array(u8.buffer, u8.byteOffset, Math.floor(u8.byteLength / 2));
  const out = new Float32Array(i16.length);
  for (let i = 0; i < i16.length; i += 1) out[i] = i16[i] / 32768.0;
  return out;
}

function ensureAudioContext(preferredSampleRate = 24000) {
  if (!currentAudioContext) {
    currentAudioContext = new (window.AudioContext || window.webkitAudioContext)({
      sampleRate: preferredSampleRate,
      latencyHint: 'interactive'
    });
    console.log(`AudioContext sampleRate=${currentAudioContext.sampleRate} (requested ${preferredSampleRate})`);
  }
}

// First Audio timing tracking
let userInputStartTime = null;
let firstAudioStarted = false;

let currentSystemPrompt = '';
let systemPromptRequestInFlight = false;

function getVisionImageSize() {
  return null;
}

async function startAudioOnly() {
  try {
    // Only request audio access, no video
    mediaStream = await navigator.mediaDevices.getUserMedia({
      audio: true
    });

    audioTracks = mediaStream.getAudioTracks();
    toggleMicrophone(true);
    console.log('Audio-only mode initialized for LLM');
  } catch (error) {
    console.error('Error accessing audio devices.', error);
  }
}

function setupCameraContainer() {}

function resizeContainerForImage(imageElement) {}

// Clear uploaded image and return to webcam
function clearUploadedImage() {}


// Clear captured image and return to live camera feed
function clearCapturedImage() {}

// Microphone functionality now handled by recordButton above

// New event handlers for dashboard buttons
if (sendButton) {
  sendButton.addEventListener('click', () => {
    sendTextMessage();
  });
}

if (recordButton) {
  recordButton.addEventListener('click', () => {
    isMicrophoneMuted = !isMicrophoneMuted;
    toggleMicrophone(isMicrophoneMuted);
  });
}

// Theme Toggle
if (themeToggle) {
  themeToggle.addEventListener('click', () => {
    toggleTheme();
  });
}

// Chat overlay buttons
const newChatButton = document.getElementById('newChatButton');
const abortButton = document.getElementById('abortButton');

if (newChatButton) {
  newChatButton.addEventListener('click', () => {
    newChat();
  });
}

if (abortButton) {
  abortButton.addEventListener('click', () => {
    abortResponse();
  });
}

// Enter key support for message input
if (messageInput) {
  messageInput.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' && !event.shiftKey) {
      event.preventDefault();
      sendTextMessage();
    }
  });
}

if (systemPromptButton && systemPromptModal && systemPromptTextarea && systemPromptSave && systemPromptCancel) {
  systemPromptButton.addEventListener('click', openSystemPromptModal);
  systemPromptCancel.addEventListener('click', closeSystemPromptModal);
  systemPromptSave.addEventListener('click', saveSystemPrompt);
  systemPromptModal.addEventListener('click', handleSystemPromptBackdropClick);
  document.addEventListener('keydown', handleSystemPromptKeydown);
}

let mediaRecorder;
let audioBlob;
let audioUrl;

window.onload = function () {
  startAudioOnly();

  // Initialize dashboard functionality
  fetchSystemPrompt();
  syncControlsForMode();
  if (modeSelect) {
    modeSelect.addEventListener('change', syncControlsForMode);
  }

  // Initialize chat history checkbox behavior
  const chatHistoryCheckbox = document.getElementById('toggleChatHistory');
  if (chatHistoryCheckbox) {
    chatHistoryCheckbox.checked = true;
    addChatMessage("Hi, this is the SiMa GenAI Demo! Chat history is enabled.", false);
    chatHistoryCheckbox.addEventListener('change', handleChatHistoryToggle);
  } else {
    // Fallback if checkbox doesn't exist
    addChatMessage("Hi, this is the SiMa GenAI Demo!", false);
  }
};

function getSelectedMode() {
  return modeSelect ? modeSelect.value : 'asr';
}

function syncControlsForMode() {
  const mode = getSelectedMode();
  const isTts = mode === 'tts';
  const needsAudio = mode === 'asr' || mode === 'interleaved';

  if (voiceRow) {
    voiceRow.style.display = isTts || mode === 'interleaved' ? 'block' : 'none';
  }
  if (messageInput) {
    messageInput.disabled = needsAudio;
    messageInput.placeholder = needsAudio
      ? 'Use microphone recording for this mode...'
      : 'Type your message...';
  }
}

function toggleMicrophone(mute) {
  const mutedUrl = recordButton.getAttribute('data-muted-url');
  const activeUrl = recordButton.getAttribute('data-active-url');

  recordIcon.src = mute ? mutedUrl : activeUrl;

  if (audioTracks.length > 0) {
    audioTracks[0].enabled = !mute;
  }

  if (!mute) {
    recordButton.classList.add('recording');
    startRecording();
  } else {
    recordButton.classList.remove('recording');
    stopRecording();
  }
}

function updateSystemPromptButtonLabel() {
  if (!systemPromptButton) return;
  systemPromptButton.textContent = currentSystemPrompt ? 'Edit System Prompt' : 'Set System Prompt';
}

function openSystemPromptModal() {
  if (!systemPromptModal || !systemPromptTextarea) return;
  if (systemPromptRequestInFlight) return;

  systemPromptTextarea.value = currentSystemPrompt;
  if (systemPromptModalMessage) {
    systemPromptModalMessage.textContent = '';
    systemPromptModalMessage.classList.remove('error');
  }
  systemPromptModal.style.display = 'flex';
  setTimeout(() => systemPromptTextarea.focus(), 50);
}

function closeSystemPromptModal() {
  if (!systemPromptModal) return;
  systemPromptModal.style.display = 'none';
  if (systemPromptModalMessage) {
    systemPromptModalMessage.textContent = '';
    systemPromptModalMessage.classList.remove('error');
  }
}

function handleSystemPromptBackdropClick(event) {
  if (event.target === systemPromptModal) {
    closeSystemPromptModal();
  }
}

function handleSystemPromptKeydown(event) {
  if (event.key === 'Escape' && systemPromptModal && systemPromptModal.style.display === 'flex') {
    closeSystemPromptModal();
  }
}

async function fetchSystemPrompt() {
  if (!systemPromptButton) return;
  try {
    systemPromptRequestInFlight = true;
    systemPromptButton.disabled = true;
    const response = await fetch('/system-prompt');
    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }
    const data = await response.json();
    currentSystemPrompt = (data && typeof data.system_prompt === 'string') ? data.system_prompt : '';
    updateSystemPromptButtonLabel();
  } catch (error) {
    console.error('System prompt fetch failed:', error);
  } finally {
    systemPromptRequestInFlight = false;
    systemPromptButton.disabled = false;
  }
}

async function saveSystemPrompt() {
  if (!systemPromptTextarea || systemPromptRequestInFlight) return;
  const newPrompt = systemPromptTextarea.value.trim();

  try {
    systemPromptRequestInFlight = true;
    if (systemPromptButton) systemPromptButton.disabled = true;
    if (systemPromptSave) systemPromptSave.disabled = true;
    if (systemPromptCancel) systemPromptCancel.disabled = true;
    if (systemPromptModalMessage) {
      systemPromptModalMessage.textContent = 'Saving...';
      systemPromptModalMessage.classList.remove('error');
    }

    const response = await fetch('/system-prompt', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ system_prompt: newPrompt })
    });

    if (!response.ok) {
      throw new Error(`HTTP ${response.status}`);
    }

    const data = await response.json();
    currentSystemPrompt = (data && typeof data.system_prompt === 'string') ? data.system_prompt : '';
    updateSystemPromptButtonLabel();
    closeSystemPromptModal();
  } catch (error) {
    if (systemPromptModalMessage) {
      systemPromptModalMessage.textContent = 'Failed to save system prompt.';
      systemPromptModalMessage.classList.add('error');
    }
    console.error('System prompt save failed:', error);
  } finally {
    systemPromptRequestInFlight = false;
    if (systemPromptButton) systemPromptButton.disabled = false;
    if (systemPromptSave) systemPromptSave.disabled = false;
    if (systemPromptCancel) systemPromptCancel.disabled = false;
  }
}
function getSupportedMimeType() {
  const possibleTypes = [
    'audio/webm;codecs=opus',
    'audio/webm',
    'audio/ogg;codecs=opus',
    'audio/ogg',
    'audio/wav'
  ];
  return possibleTypes.find(type => MediaRecorder.isTypeSupported(type)) || '';
}

function startRecording() {
  const mimeType = getSupportedMimeType();

  if (!mimeType) {
    console.error('No supported MIME type found for MediaRecorder.');
    return;
  }

  try {
    recordedChunks = [];

    // Force audio-only tracks for safer recording
    const audioStream = new MediaStream(mediaStream.getAudioTracks());
    console.log('Audio Stream:', audioStream);

    mediaRecorder = new MediaRecorder(audioStream, { mimeType });

    mediaRecorder.ondataavailable = (event) => {
      if (event.data.size > 0) {
        recordedChunks.push(event.data);
      }
    };

    mediaRecorder.onerror = (event) => {
      console.error('MediaRecorder encountered an error:', event.error);
    };

    mediaRecorder.onstop = saveRecording;
    mediaRecorder.start();
    console.log('Recording started with MIME type:', mimeType);
  } catch (error) {
    console.error('Failed to start recording:', error.message);
    console.error('Error name:', error.name);
    console.error('Error stack:', error.stack);
  }
}

function stopRecording() {
  if (mediaRecorder && mediaRecorder.state !== 'inactive') {
    mediaRecorder.stop();
    console.log('Recording stopped...');
  }
}

function saveRecording() {
  audioBlob = new Blob(recordedChunks, { type: mediaRecorder.mimeType });
  audioUrl = URL.createObjectURL(audioBlob);
  clearIfSingleShotMode();
  startProcessing('', null, false);
}

function getFileExtension(mimeType) {
  switch (mimeType) {
    case 'audio/webm':
    case 'audio/webm;codecs=opus':
      return 'webm';
    case 'audio/ogg':
    case 'audio/ogg;codecs=opus':
      return 'ogg';
    case 'audio/wav':
      return 'wav';
    default:
      return 'audio';
  }
}

async function stop(stopAudioFlag = false) {
  if (stopAudioFlag) {
    stopAudio();
    audioQueue.length = 0;
    isPlaying = false;
  }

  try {
    const response = await fetch('/stop', { method: 'POST' });
    if (!response.ok) {
      console.error(`Backend stop request failed with status ${response.status}`);
    } else {
      console.log('Sent stop request to backend');
    }
  } catch (err) {
    console.error('Failed to send stop request:', err);
  }
}

// Chat message management
let chatHistory = [];
let lastCapturedImageDataUrl = null;
let currentVisualizer = null; // Track current running visualizer
let visualizerStopTimeout = null; // Delay stopping visualizer

// Helper function to get current image data URL for chat preview
function getCurrentImageDataUrl() {
  // First priority: use the last captured image from snap/capture if available
  if (lastCapturedImageDataUrl) {
    return lastCapturedImageDataUrl;
  }

  // Second priority: check for uploaded image
  const imageOverlay = document.getElementById('imageOverlay');
  if (imageOverlay && imageOverlay.style.display === 'block' && imageOverlay.src) {
    return imageOverlay.src;
  }

  // Third priority: check for captured image overlay (fallback)
  const capturedImageOverlay = document.getElementById('capturedImageOverlay');
  if (capturedImageOverlay && capturedImageOverlay.src) {
    return capturedImageOverlay.src;
  }

  // Last resort: capture current webcam frame
  const cameraPreview = document.getElementById('cameraPreview');
  if (cameraPreview && cameraPreview.videoWidth > 0) {
    const canvas = document.createElement('canvas');
    const context = canvas.getContext('2d');
    canvas.width = cameraPreview.videoWidth;
    canvas.height = cameraPreview.videoHeight;
    context.drawImage(cameraPreview, 0, 0, canvas.width, canvas.height);
    return canvas.toDataURL('image/png');
  }

  return null;
}

function addChatMessage(message, isUser = false, includeImagePreview = false) {
  // Add text message first
  const messageDiv = document.createElement('div');
  messageDiv.className = `message ${isUser ? 'user' : 'assistant'}`;
  messageDiv.textContent = message;

  chatMessages.appendChild(messageDiv);

  // Add image preview as separate message after text if needed
  if (isUser && includeImagePreview) {
    const imageDataUrl = getCurrentImageDataUrl();
    if (imageDataUrl) {
      const imageMessageDiv = document.createElement('div');
      imageMessageDiv.className = 'message user image-message';

      const imagePreview = document.createElement('img');
      imagePreview.className = 'message-image-preview';
      imagePreview.src = imageDataUrl;
      imagePreview.alt = 'Image used in query';
      imagePreview.style.cssText = `
        display: block;
        width: 100%;
        height: auto;
        max-width: 150px;
        max-height: 150x;
        border-radius: 15px;
        border: 1px solid var(--border-color);
        cursor: pointer;
        object-fit: cover;
      `;

      // Add click handler to open modal
      imagePreview.addEventListener('click', (e) => {
        e.stopPropagation();
        openImageModal(imageDataUrl);
      });

      imageMessageDiv.appendChild(imagePreview);
      chatMessages.appendChild(imageMessageDiv);
    }
  }
  chatMessages.scrollTop = chatMessages.scrollHeight;

  // Store in history
  chatHistory.push({ message, isUser, timestamp: Date.now() });
  return messageDiv;
}

// Helper function to create assistant message placeholder
function createAssistantMessage() {
  const assistantMessage = document.createElement('div');
  assistantMessage.className = 'message assistant streaming-text';
  assistantMessage.textContent = 'Processing...';


  chatMessages.appendChild(assistantMessage);
  chatMessages.scrollTop = chatMessages.scrollHeight;

  // Show abort button when assistant message is created
  showAbortButton();
}

// Helper function to clear messages if in single-shot mode or pending context clear
function clearIfSingleShotMode() {
  const chatHistoryCheckbox = document.getElementById('toggleChatHistory');
  const shouldClear = (chatHistoryCheckbox && !chatHistoryCheckbox.checked) || window.pendingContextClear;

  if (shouldClear) {
    // Clear previous messages (single-shot mode or context limit was hit)
    const messages = chatMessages.querySelectorAll('.message');
    messages.forEach(message => message.remove());
    chatHistory = [];
    pendingTranscriptionMessage = null;

    // Reset the pending clear flag
    if (window.pendingContextClear) {
      window.pendingContextClear = false;
      console.log('UI cleared after context limit reset');
    }

    return true;
  }
  return false;
}

function sendTextMessage() {
  const mode = getSelectedMode();
  if (mode !== 'tts') {
    addChatMessage('Text input is only supported in TTS mode. Use the microphone for ASR or Interleaved.', false);
    return;
  }
  const message = messageInput.value.trim();
  if (!message) return;

  // Clear input first
  messageInput.value = '';

  clearIfSingleShotMode();
  addChatMessage(message, true, false);
  startProcessing('', message);
}

function clearChatHistory() {
  chatHistory = [];
  pendingTranscriptionMessage = null;
  // Clear the stored captured image
  lastCapturedImageDataUrl = null;

  // Remove only chat messages, preserve overlay buttons
  const messages = chatMessages.querySelectorAll('.message');
  messages.forEach(message => message.remove());
}

// Handle chat history checkbox toggle
function handleChatHistoryToggle(event) {
  if (!event.target.checked) {
    // Checkbox was disabled - clear everything
    console.log('Chat history disabled - clearing history');

    // Clear UI messages
    clearChatHistory();

    // Clear backend history
    fetch('/clear-history', { method: 'POST' })
      .then(response => response.json())
      .then(data => {
        console.log('Backend conversation history cleared:', data);
      })
      .catch(error => {
        console.error('Failed to clear backend history:', error);
      });

    // Add info message
    addChatMessage("Hi, this is the SiMa GenAI Demo! Chat history disabled. Enable 'Include chat history' in settings for multi-turn conversations.", false);
  } else {
    // Checkbox was re-enabled - keep user conversation, update info message if no conversation
    console.log('Chat history enabled - conversations will accumulate');

    // Check if there are any USER messages (actual conversation)
    const userMessages = chatMessages.querySelectorAll('.message.user');
    if (userMessages.length === 0) {
      // No user conversation - clear info messages and show new status
      const allMessages = chatMessages.querySelectorAll('.message');
      allMessages.forEach(message => message.remove());
      addChatMessage("Hi, this is the SiMa GenAI Demo! Chat history enabled. Conversations will be remembered.", false);
    }
    // If there are user messages, keep them and don't add any info message
  }
}

// New Chat functionality
function newChat() {
  // Clear chat history
  clearChatHistory();

  // Clear backend conversation history
  fetch('/clear-history', { method: 'POST' })
    .then(response => response.json())
    .then(data => {
      console.log('Backend conversation history cleared:', data);
    })
    .catch(error => {
      console.error('Failed to clear backend history:', error);
    });

  // Re-add welcome message
  addChatMessage("Hi, this is the SiMa GenAI Demo!", false);

  // Hide abort button if visible
  hideAbortButton();
}

// Abort current response
function abortResponse() {
  responseAborted = true;

  // Stop backend processing and audio
  void stop(true);
  // Retry stop shortly after to handle race where backend run thread
  // starts just after the initial /stop request is processed.
  setTimeout(() => {
    if (responseAborted) {
      void stop(false);
    }
  }, 300);

  // Remove speaking indicator from current assistant message
  const assistantMessages = chatMessages.querySelectorAll('.message.assistant');
  const currentAssistantMessage = assistantMessages[assistantMessages.length - 1];
  if (currentAssistantMessage) {
    currentAssistantMessage.classList.remove('speaking');

    // Hide the audio visualizer
    currentAssistantMessage.classList.remove('audio-playing');
    const canvas = currentAssistantMessage.querySelector('.audio-visualizer');
    if (canvas) {
      canvas.style.display = 'none';
    }

    // Stop current visualizer if it matches this message
    if (currentVisualizer && currentVisualizer.message === currentAssistantMessage) {
      currentVisualizer = null;
    }
  }

  // Hide the abort button
  hideAbortButton();
}

// Show/hide abort button helpers
function showAbortButton() {
  if (abortButton) {
    abortButton.style.display = 'flex';
  }
}

function hideAbortButton() {
  if (abortButton) {
    abortButton.style.display = 'none';
  }
}

function captureAndAnimateSnap(textchat = null) {
  clearIfSingleShotMode();
  if (textchat) {
    addChatMessage(textchat, true, false);
    startProcessing('', textchat, false);
    return;
  }
  startProcessing('', null, true);
}


function startProcessing(resultMessage, textchat = null, waitForTranscription = false) {
  responseAborted = false;
  pendingTranscriptionMessage = null;

  // Reset metrics
  document.getElementById('firstTokenTime').textContent = '...';
  document.getElementById('tpsValue').textContent = '...';
  document.getElementById('rtfValue').textContent = '...';
  document.getElementById('transcribeTime').textContent = '...';
  document.getElementById('firstAudioTime').textContent = '...';

  // Reset First Audio timing
  userInputStartTime = Date.now();
  firstAudioStarted = false;

  // Re-enable audio for new query (in case it was aborted previously)
  shouldPlayAudio = true;
  receivedEndSignal = false;
  audioQueue.length = 0;
  decodedAudioQueue.length = 0;
  decodeInFlight = false;
  scheduledSources = 0;
  nextPlaybackTime = 0;
  playbackStarted = false;

  const includeChatHistory = document.getElementById('toggleChatHistory');
  const formData = new FormData();
  const mode = getSelectedMode();
  const selectedVoice = voiceSelect ? voiceSelect.value : 'uk_female';

  formData.append('mode', mode);
  formData.append('voice', selectedVoice);
  formData.append('includeChatHistory', includeChatHistory ? includeChatHistory.checked : true);

  if (mode === 'asr') {
    if (!audioBlob) {
      addChatMessage('ASR mode requires microphone audio input.', false);
      return;
    }
    formData.append('audio_data', audioBlob, 'audio.webm');
  } else if (mode === 'tts') {
    const textValue = (textchat || '').trim();
    if (!textValue) {
      addChatMessage('TTS mode requires a text prompt.', false);
      return;
    }
    formData.append('textchat', textValue);
  } else {
    if (!audioBlob) {
      addChatMessage('Interleaved mode requires microphone audio input.', false);
      return;
    }
    formData.append('audio_data', audioBlob, 'audio.webm');
    const interleavedText = currentSystemPrompt.trim();
    if (interleavedText) {
      formData.append('textchat', interleavedText);
    }
  }

  // Create placeholder for assistant response (unless waiting for transcription)
  if (!waitForTranscription) {
    createAssistantMessage();
  }

  const sendRequest = () => {

    fetch('/upload', {
      method: 'POST',
      body: formData
    })
      .then(response => response.json())
      .then(data => {
        receivedEndSignal = false;
        displayResult(data.ttt || 0);
      })
      .catch(error => {
        console.error('Error uploading files:', error);
        displayResult();
      });
  };
  sendRequest();
}

// Brutally clean up all global audio handles
function stopAudio() {
  shouldPlayAudio = false;
  isPlaying = false;
  audioQueue.length = 0;
  decodedAudioQueue.length = 0;
  decodeInFlight = false;
  scheduledSources = 0;
  nextPlaybackTime = 0;
  playbackStarted = false;

  try {
    if (currentSourceNode) {
      currentSourceNode.stop(0);
      currentSourceNode.disconnect();
    }
  } catch (e) {
    console.warn("Error stopping source node:", e);
  } finally {
    currentSourceNode = null;
  }

  try {
    if (currentAudioContext) {
      currentAudioContext.close();
    }
  } catch (e) {
    console.warn("Error closing audio context:", e);
  } finally {
    currentAudioContext = null;
  }

  console.log("🧹 Audio playback completely stopped and cleaned up.");
}

let currentStreamingMessage = null;
let pendingTranscriptionMessage = null;

function displayResult(ttt = 0) {
  // Only update metrics - text display is now handled by WebSocket events
  // The assistant message should already exist from startProcessing()
  document.getElementById('transcribeTime').textContent = ttt + 's';
}


function selectImage() {
  return;
}

socket.on('audio_chunk', (data) => {
  // Reject chunks if audio playback was aborted
  if (!shouldPlayAudio) {
    console.log('Ignoring audio chunk - playback aborted');
    return;
  }

  const text = data.text;

  console.log('Received text & audio :', text);

  document.getElementById('tpsValue').textContent = data.tps;
  document.getElementById('rtfValue').textContent = data.rtf;

  if (typeof data.audio_pcm_i16_b64 === 'string' && data.audio_pcm_i16_b64.length > 0) {
    const pcm = pcm16B64ToFloat32(data.audio_pcm_i16_b64);
    const sampleRateHz = Number(data.sample_rate_hz || 24000);
    audioQueue.push({ kind: 'pcm', pcm, sampleRateHz });
    void processAudioQueue();
    return;
  }

  // Backward-compatible fallback: WAV bytes payload.
  if (data.audio) {
    audioQueue.push({ kind: 'wav', wavBytes: data.audio });
    void processAudioQueue();
  }
});

// Handle system audio (e.g., voice switch confirmation) regardless of panel
socket.on('system_audio', (data) => {
  const audioData = data.audio;
  if (!audioData) return;
  // Play system audio immediately, bypassing panel3 checks
  processSystemAudioOnce(audioData);
});

// Handle standalone 'tps' event
socket.on('tps', (data) => {
  if (data !== undefined) {
    document.getElementById('tpsValue').textContent = data.toFixed(2);
  }
});

// Handle standalone 'ttnt' event
socket.on('ttnt', (data) => {
  if (data !== undefined) {
    document.getElementById('tpsValue').textContent = data.toFixed(2);
  }
});


function handleTextUpdate(data) {
  if (responseAborted) {
    return;
  }

  if (data && data.results) {
    const cleanText = data.results.replace(/<\/s>|<pad>|<0x[0-9A-Fa-f]+>/g, '');
    if (!cleanText) return;

    // Find the current assistant message (last message with 'assistant' class)
    const assistantMessages = chatMessages.querySelectorAll('.message.assistant');
    const currentAssistantMessage = assistantMessages[assistantMessages.length - 1];

    if (!currentAssistantMessage) {
      console.warn('No assistant message found to update');
      return;
    }

    // Check if this is the first token (message shows "Processing...")
    if (currentAssistantMessage.textContent === 'Processing...') {
      // Create separate text container to preserve canvas
      const textSpan = document.createElement('span');
      textSpan.className = 'message-text';

      // Preserve canvas when restructuring
      const canvas = currentAssistantMessage.querySelector('.audio-visualizer');
      currentAssistantMessage.innerHTML = ''; // Clear everything

      // Add text container and canvas back
      currentAssistantMessage.appendChild(textSpan);
      if (canvas) {
        currentAssistantMessage.appendChild(canvas);
        console.log('🔧 Restructured message with separate text container');
      }
      currentAssistantMessage.classList.add('speaking');
    }

    // Append text to the text container, not the message directly
    const textContainer = currentAssistantMessage.querySelector('.message-text') || currentAssistantMessage;
    if (textContainer.className === 'message-text') {
      textContainer.textContent += cleanText;
    } else {
      // Fallback for messages without text container
      currentAssistantMessage.textContent += cleanText;
    }

    // Scroll chat to bottom
    scrollChatToBottom();
  }
}

// Helper function to scroll chat to bottom
function scrollChatToBottom() {
  chatMessages.scrollTop = chatMessages.scrollHeight;
}

function displayTranscribedQuery(text) {
  if (!text) return;

  if (pendingTranscriptionMessage) {
    pendingTranscriptionMessage.textContent = text;
    pendingTranscriptionMessage.classList.remove('streaming-text');
    pendingTranscriptionMessage = null;
  } else {
    addChatMessage(text, false, false);
  }

  // Scroll chat to bottom
  scrollChatToBottom();
}

function handleTranscriptionChunk(data) {
  if (responseAborted || !data || typeof data.text !== 'string') {
    return;
  }

  if (!pendingTranscriptionMessage) {
    pendingTranscriptionMessage = addChatMessage('', false, false);
    pendingTranscriptionMessage.classList.add('streaming-text');
  }

  pendingTranscriptionMessage.textContent = data.text;
  scrollChatToBottom();
}

function handleTtfsUpdate(data) {
  if (data) {
    document.getElementById('firstTokenTime').textContent = data + 's';
  }
}

function handleTranscriptionTimeUpdate(data) {
  if (data) {
    document.getElementById('transcribeTime').textContent = data + 's';
  }
}

socket.on('talk', handleTextUpdate);
socket.on('update', handleTextUpdate);
socket.on('end', (data) => {
  console.log('Received end event:', data);
  receivedEndSignal = true;

  // Remove speaking indicator from current assistant message
  const assistantMessages = chatMessages.querySelectorAll('.message.assistant');
  const currentAssistantMessage = assistantMessages[assistantMessages.length - 1];
  if (currentAssistantMessage) {
    currentAssistantMessage.classList.remove('speaking');

    // Don't remove audio-playing class here - let actual audio end handle it
    // The 'end' event is for text streaming, not audio playback
  }

  // Don't hide abort button here - keep it visible during audio playback
  // hideAbortButton(); // Moved to audio completion
  void processAudioQueue();
});
socket.on('ttfs', handleTtfsUpdate);
socket.on('transcription-time', handleTranscriptionTimeUpdate);
socket.on('transcription_chunk', handleTranscriptionChunk);
socket.on('transcription', displayTranscribedQuery);
socket.on('repetitive', stop);

// Handle context limit reached - backend history already cleared
socket.on('context_full', (data) => {
  console.log('Context limit reached:', data);

  // Add warning message to UI
  addChatMessage("⚠️ Context limit reached. History has been cleared - your next message will start a fresh conversation.", false);

  // Set flag to clear UI on next message send
  window.pendingContextClear = true;
});

async function processAudioQueue() {
  if (!shouldPlayAudio) return;

  const firstItem = audioQueue.length > 0 ? audioQueue[0] : null;
  const preferredRate = firstItem && firstItem.kind === 'pcm' ? firstItem.sampleRateHz : 24000;
  ensureAudioContext(preferredRate);

  if (!decodeInFlight) {
    decodeInFlight = true;
    try {
      while (audioQueue.length > 0 && shouldPlayAudio) {
        const item = audioQueue.shift();
        if (!item) continue;
        if (item.kind === 'pcm') {
          const pcm = item.pcm;
          const sampleRateHz = Number(item.sampleRateHz || 24000);
          const audioBuffer = currentAudioContext.createBuffer(1, pcm.length, sampleRateHz);
          audioBuffer.copyToChannel(pcm, 0);
          decodedAudioQueue.push(audioBuffer);
        } else {
          const blob = new Blob([item.wavBytes], { type: 'audio/wav' });
          const arrayBuffer = await blob.arrayBuffer();
          const audioBuffer = await currentAudioContext.decodeAudioData(arrayBuffer);
          decodedAudioQueue.push(audioBuffer);
        }
      }
    } catch (err) {
      console.warn('Audio decode failed:', err);
    } finally {
      decodeInFlight = false;
    }
  }

  if (!playbackStarted) {
    if (decodedAudioQueue.length < AUDIO_PREROLL_CHUNKS && !receivedEndSignal) {
      return;
    }
    playbackStarted = true;
    nextPlaybackTime = Math.max(currentAudioContext.currentTime + 0.03, nextPlaybackTime);
  }

  while (decodedAudioQueue.length > 0 && shouldPlayAudio) {
    const audioBuffer = decodedAudioQueue.shift();
    const source = currentAudioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(currentAudioContext.destination);

    const startAt = Math.max(nextPlaybackTime, currentAudioContext.currentTime + 0.01);
    nextPlaybackTime = startAt + audioBuffer.duration;
    scheduledSources += 1;
    isPlaying = true;
    currentSourceNode = source;

    if (!firstAudioStarted && userInputStartTime) {
      const firstAudioTime = (Date.now() - userInputStartTime) / 1000;
      document.getElementById('firstAudioTime').textContent = firstAudioTime.toFixed(2) + 's';
      firstAudioStarted = true;
    }

    source.onended = () => {
      scheduledSources = Math.max(0, scheduledSources - 1);
      if (scheduledSources === 0) {
        isPlaying = false;
        currentSourceNode = null;
        if (receivedEndSignal && audioQueue.length === 0 && decodedAudioQueue.length === 0 && !decodeInFlight) {
          hideAbortButton();
        }
      }
      if (shouldPlayAudio) {
        setTimeout(() => { void processAudioQueue(); }, 0);
      }
    };

    source.start(startAt);
  }
}

async function processSystemAudioOnce(data) {
  try {
    const blob = new Blob([data], { type: 'audio/wav' });
    const arrayBuffer = await blob.arrayBuffer();

    ensureAudioContext(24000);
    const audioBuffer = await currentAudioContext.decodeAudioData(arrayBuffer);

    const source = currentAudioContext.createBufferSource();
    source.buffer = audioBuffer;
    source.connect(currentAudioContext.destination);
    source.start();
  } catch (e) {
    console.warn('System audio playback failed:', e);
  }
}

function toggleImageButtons(enabled) {
  return enabled;
}

function toggleCameraDisplay(imageEnabled) {
  return imageEnabled;
}

// Theme Management Functions
function getStoredTheme() {
  return localStorage.getItem('theme') || 'dark';
}

function setStoredTheme(theme) {
  localStorage.setItem('theme', theme);
}

function updateLogoForTheme() {
  const topCenterIcon = document.getElementById('topCenterIcon');
  if (topCenterIcon) {
    const currentTheme = document.documentElement.getAttribute('data-theme');
    if (currentTheme === 'light') {
      topCenterIcon.src = 'static/icons/logo_dark.png';
    } else {
      topCenterIcon.src = 'static/icons/logo_bright.png';
    }
  }
}

function applyTheme(theme) {
  document.documentElement.setAttribute('data-theme', theme);
  updateThemeIcon(theme);
  updateLogoForTheme();
}

function updateThemeIcon(theme) {
  if (themeIcon) {
    themeIcon.textContent = theme === 'light' ? '☀️' : '🌙';
  }
}

function toggleTheme() {
  const currentTheme = getStoredTheme();
  const newTheme = currentTheme === 'light' ? 'dark' : 'light';

  setStoredTheme(newTheme);
  applyTheme(newTheme);

  console.log(`🎨 Theme switched to: ${newTheme}`);
}

// Initialize theme on page load
function initializeTheme() {
  const savedTheme = getStoredTheme();
  applyTheme(savedTheme);
  console.log(`🎨 Theme initialized: ${savedTheme}`);
}

// Call theme initialization when DOM is ready
document.addEventListener('DOMContentLoaded', () => {
  initializeTheme();

  // Clear chat history on page refresh
  fetch('/clear-history', { method: 'POST' })
    .catch(error => console.log('Failed to clear history on page load:', error));
});

// Image Modal Functions
function openImageModal(imageSrc) {
  const modal = document.getElementById('imageModal');
  const modalImage = document.getElementById('modalImage');
  if (!modal || !modalImage) return;

  modalImage.src = imageSrc;
  modal.style.display = 'flex';

  // Add click-outside-to-close functionality
  modal.addEventListener('click', closeImageModal);

  // Prevent closing when clicking on the image itself
  modalImage.addEventListener('click', (e) => {
    e.stopPropagation();
  });
}

function closeImageModal() {
  const modal = document.getElementById('imageModal');
  if (!modal) return;
  modal.style.display = 'none';

  // Remove event listeners to prevent memory leaks
  modal.removeEventListener('click', closeImageModal);
}
