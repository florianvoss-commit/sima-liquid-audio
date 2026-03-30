#########################################################
# Copyright (C) 2024 SiMa Technologies, Inc.
#
# This material is SiMa proprietary and confidential.
#
# This material may not be copied or distributed without
# the express prior written permission of SiMa.
#
# All rights reserved.
#########################################################
import argparse
import base64
import http.server
import json
import logging
import os
import requests
import shutil
import socketserver
import sys
import threading
import queue
import time
from datetime import datetime, timezone
from queue import Queue
from typing import Any

# Flask imports
from flask import Flask, Response, render_template, jsonify, request, stream_with_context
from flask_socketio import SocketIO
from flask_cors import CORS

from pathlib import Path

model_stream_queue = Queue()

camera = None
genai_app = None

class AppConstants:
    DEFAULT_SIMA_SERVER_IP = "127.0.0.1:9998"
    DEFAULT_CAMERA_IDX = 0
    DEFAULT_MODEL_QUERY_STR='Describe what you see in the picture.'
    DEFAULT_HTTP_PORT = 8081
    DEFAULT_CAMERA_IDX = 0
    DEFAULT_UPLOADS_DIR = 'uploads'

def parse_vision_image_size(size_str):
    if not size_str:
        return None

    normalized = str(size_str).strip().lower().replace(' ', '')
    if not normalized:
        return None

    height = width = None

    if 'x' in normalized:
        parts = normalized.split('x', 1)
        if len(parts) == 2:
            try:
                height = int(float(parts[0]))
                width = int(float(parts[1]))
            except ValueError:
                return None
    else:
        try:
            value = int(float(normalized))
            height = width = value
        except ValueError:
            return None

    if not height or not width or height <= 0 or width <= 0:
        return None

    return {'height': height, 'width': width}

def clear_model_queue():
    """Drain the queue to remove leftover data from previous runs."""
    cleared = 0
    while not model_stream_queue.empty():
        try:
            model_stream_queue.get_nowait()
            model_stream_queue.task_done()
            cleared += 1
        except queue.Empty:
            break
    if cleared:
        logging.info(f"Cleared {cleared} leftover items from LLaVa queue.")


def _sanitize_stream_text(text: str) -> str:
    return text.replace('*', '').replace('＊', '').replace('\n', '')


def _iter_sse_events(response: requests.Response):
    response.encoding = "utf-8"
    event_name = None
    data_lines: list[str] = []

    for raw_line in response.iter_lines(decode_unicode=True):
        line = raw_line or ""
        if not line:
            if event_name is not None and data_lines:
                payload = "\n".join(data_lines)
                try:
                    yield event_name, json.loads(payload)
                except json.JSONDecodeError:
                    logging.warning("Invalid SSE JSON payload for event '%s': %s", event_name, payload)
            event_name = None
            data_lines = []
            continue

        if line.startswith(":"):
            continue
        if line.startswith("event:"):
            event_name = line.partition(":")[2].strip()
        elif line.startswith("data:"):
            data_lines.append(line.partition(":")[2].lstrip())


class AppContext:
    def __init__(self):
        self.app = None
        self.socketio = None
        self.system_prompt = None
        self.model_display_name = ""
        self.vision_image_size = None
        self._audio_stream_lock = threading.Lock()
        self._active_audio_stream_id = 0

        # Conversation history for OpenAI-style chat
        self.conversation_history = []
        self.current_response = ""  # Accumulate raw LLM response during streaming

    def _build_system_prompt_message(self):
        if not self.system_prompt:
            return None
        return {"role": "system", "content": self.system_prompt}

    def set_system_prompt(self, prompt=None):
        normalized = (prompt or '').strip()
        self.system_prompt = normalized if normalized else None
        # Update or remove the system message in-place
        existing_index = next(
            (idx for idx, message in enumerate(self.conversation_history) if message.get('role') == 'system'),
            None
        )
        system_message = self._build_system_prompt_message()
        if system_message:
            if existing_index is not None:
                self.conversation_history[existing_index] = system_message
            else:
                self.conversation_history.insert(0, system_message)
        elif existing_index is not None:
            self.conversation_history.pop(existing_index)

        if self.system_prompt:
            logging.info("System prompt updated.")
        else:
            logging.info("System prompt cleared.")

    def get_system_prompt(self):
        return self.system_prompt or ""

    def add_user_message(self, content):
        """Add a text-only user message to conversation history."""
        self.conversation_history.append({"role": "user", "content": content})
        logging.info(
            "Added user message (text-only). "
            f"Total messages: {len(self.conversation_history)}"
        )


    def start_assistant_response(self):
        """Start accumulating a new assistant response."""
        self.current_response = ""

    def add_to_current_response(self, text):
        """Add text to the current assistant response being accumulated."""
        self.current_response += text

    def finish_assistant_response(self):
        """Finish the current assistant response and add it to history."""
        if self.current_response:
            self.conversation_history.append({"role": "assistant", "content": self.current_response})
            logging.info(f"Added assistant response to history. Total messages: {len(self.conversation_history)}")
            self.current_response = ""

    def clear_conversation_history(self):
        """Clear the conversation history."""
        self.conversation_history = []
        self.current_response = ""
        logging.info("Conversation history cleared")
        # Reinsert system prompt if present
        system_message = self._build_system_prompt_message()
        if system_message:
            self.conversation_history.insert(0, system_message)

    def get_conversation_history(self):
        """Get the current conversation history."""
        return self.conversation_history.copy()


    def update_settings(self, camidx, model_server_ip, ragserver, httponly, model_name=None, vision_image_size=None):
        self.camidx = AppConstants.DEFAULT_CAMERA_IDX if camidx is None else camidx
        self.model_server_ip = AppConstants.DEFAULT_SIMA_SERVER_IP if model_server_ip is None else model_server_ip
        self.ragserver = ragserver
        self.httponly = httponly
        self.model_display_name = model_name or ""
        self.vision_image_size = vision_image_size
        self.update_config()
        # Align system prompt format with current mode
        if self.system_prompt:
            self.set_system_prompt(self.system_prompt)

    def update_config(self):
        self.app.config['CAMERA_IDX'] = self.camidx
        self.app.config['SIMAAI_IP_ADDR'] = self.model_server_ip
        self.app.config['SIMAAI_IP_PORT'] =  AppConstants.DEFAULT_HTTP_PORT
        self.app.config['UPLOAD_FOLDER'] = AppConstants.DEFAULT_UPLOADS_DIR
        self.app.config['MODEL_DISPLAY_NAME'] = self.model_display_name
        self.app.config['VISION_IMAGE_SIZE'] = self.vision_image_size

    def get_config(self):
        return self.app.config
        
    def initialize(self):
        self.app = Flask(__name__)
        CORS(self.app)
        self.socketio = SocketIO(self.app)

        if not os.path.exists(AppConstants.DEFAULT_UPLOADS_DIR):
            os.makedirs(AppConstants.DEFAULT_UPLOADS_DIR)

        # Note: setup_router() is called after update_settings()

        # Setup config.js route early so it's always available
        @self.app.route('/config.js')
        def config_js():
            height_val = ''
            width_val = ''
            if isinstance(self.vision_image_size, dict):
                height_val = str(self.vision_image_size.get('height', ''))
                width_val = str(self.vision_image_size.get('width', ''))
            # Use quotes around the values to make them strings in JavaScript
            js = (
                "window.SIMA_CONFIG=window.SIMA_CONFIG||{};"
                f"window.SIMA_CONFIG.visionImageHeight='{height_val}';"
                f"window.SIMA_CONFIG.visionImageWidth='{width_val}';"
            )
            return self.app.response_class(js, mimetype='application/javascript')

    def emit(self, ep, obj):
        self.socketio.emit(ep, obj)

    def begin_audio_stream(self) -> int:
        with self._audio_stream_lock:
            self._active_audio_stream_id += 1
            return self._active_audio_stream_id

    def invalidate_audio_stream(self):
        with self._audio_stream_lock:
            self._active_audio_stream_id += 1

    def is_audio_stream_active(self, stream_id: int) -> bool:
        with self._audio_stream_lock:
            return stream_id == self._active_audio_stream_id

    def run(self):
        if not self.httponly:
            cert_path = Path('certs/server.crt')
            key_path = Path('certs/server.key')
            if cert_path.exists() and key_path.exists():
                self.socketio.run(
                    self.app,
                    host='0.0.0.0',
                    port="5000",
                    ssl_context=(str(cert_path), str(key_path)),
                    debug=False,
                    allow_unsafe_werkzeug=True
                )
                return

            logging.info("TLS certs not found. Starting app server in plain HTTP mode.")

        self.socketio.run(self.app, host='0.0.0.0', port="5000",
                        debug=False, allow_unsafe_werkzeug=True)
            
    def run_stop(self):
        logging.info('Stopping processing...')

        try:
            success = post_stop_to_sima()
            if not success:
                logging.error('Failed to send stop signal to SiMa.ai server.')
                return False
        except Exception as e:
            logging.error(f"Failed to post stop to SiMa.ai: {e}")
            return False

        return True


    def setup_router(self):

        @self.app.route('/')
        def newui():
            self.socketio.emit('update', {"hello" : "world"})
            return render_template('newui.html',
                                 model_name=self.model_display_name)

        @self.app.route('/stop', methods=['POST'])
        def stop_processing():
            logging.info('Received /stop request. Attempting to stop processing...')
            self.invalidate_audio_stream()
            stopped = self.run_stop()
            if not stopped:
                return jsonify({'status': 'error', 'message': 'Failed to stop backend processing'}), 502
            return jsonify({'status': 'stopped'}), 200

        @self.app.route('/clear-history', methods=['POST'])
        def clear_history():
            logging.info('Received /clear-history request. Clearing conversation history...')
            self.clear_conversation_history()
            reset_ok = post_reset_conversation_to_mla()
            if not reset_ok:
                return jsonify({'status': 'error', 'message': 'Failed to reset backend conversation state'}), 502
            return jsonify({'status': 'history cleared'}), 200

        @self.app.route('/system-prompt', methods=['GET', 'POST'])
        def system_prompt():
            if request.method == 'GET':
                return jsonify({'system_prompt': self.get_system_prompt()})

            try:
                data = request.get_json(silent=True) or {}
                prompt = data.get('system_prompt', '')
                self.set_system_prompt(prompt)
                self.clear_conversation_history()
                post_reset_conversation_to_mla()
                return jsonify({'system_prompt': self.get_system_prompt()})
            except Exception as e:
                logging.error(f"System prompt update failed: {e}")
                return jsonify({'error': 'Failed to update system prompt'}), 500

        @self.app.route('/v1/chat/completions', methods=['POST'])
        @self.app.route('/chat/completions', methods=['POST'])
        def chat_completion():
            try:
                data = request.get_json()
                stream = data.get('stream', True)
                messages = data.get('messages', [])
                user_prompt = next((msg['content'] for msg in reversed(messages) if msg['role'] == 'user'), None)
                model = data.get('model', 'model')

                if not user_prompt:
                    return jsonify({'error': 'No user message provided'}), 400

                logging.info(f"Received chat completion request: {user_prompt}")

                # ✅ Clear the queue before starting a new generation
                self.run_stop()
                clear_model_queue()

                # ✅ Kick off model processing - send full conversation history to post_to_sima
                thread = threading.Thread(target=post_to_sima, args=[messages, None, None, None])
                thread.start()

                # ✅ Streaming response generator
                def stream_response():
                    try:
                        yield f'data: {{"choices":[{{"finish_reason":null,"index":0,"delta":{{"role":"assistant","content":null}}}}],"created":{int(time.time())},"id":"chatcmpl-xyz","model":"{model}","system_fingerprint":"b5780-caf5681f","object":"chat.completion.chunk"}}\n\n'
                        while True:
                            try:
                                # Wait up to 15 seconds for new tokens from model
                                chunk = model_stream_queue.get(timeout=15)
                                
                                if chunk.startswith('ttft:') or chunk.startswith("tps:"):
                                    continue

                                if chunk in ["END", "</s>"]:
                                    logging.info("Received END signal from model backend.")
                                    break  # Finish the stream

                                if chunk.startswith(' {"'):
                                    chunk = chunk.lstrip()

                                yield f'data: {{"choices":[{{"finish_reason":null,"index":0,"delta":{{"content": {json.dumps(chunk)}}}}}],"created":{int(time.time())},"id":"chatcmpl-xyz","model":"{model}","system_fingerprint":"b5780-caf5681f","object":"chat.completion.chunk"}}\n\n'
                                model_stream_queue.task_done()

                            except queue.Empty:
                                # Timeout waiting for model, send error message
                                error_message = "model backend timeout. No response received."
                                yield f"data: {json.dumps({'error': error_message})}\n\n"
                                break  # End stream on timeout

                    except Exception as e:
                        logging.error(f"Streaming error: {e}")
                        yield f"data: {json.dumps({'error': str(e)})}\n\n"

                    finally:
                        # ✅ Always clean up the queue after finishing streaming
                        clear_model_queue()
                        yield f'data: {{"choices":[{{"finish_reason":"stop","index":0,"delta":{{}}}}],"created":{int(time.time())},"id":"chatcmpl-xyz","model":"{model}","system_fingerprint":"b5780-caf5681f","object":"chat.completion.chunk","usage":{{"completion_tokens":31,"prompt_tokens":17,"total_tokens":48}},"timings":{{"prompt_n":1,"prompt_ms":18.798,"prompt_per_token_ms":18.798,"prompt_per_second":53.19714863283328,"predicted_n":31,"predicted_ms":235.214,"predicted_per_token_ms":7.587548387096774,"predicted_per_second":131.79487615533088}}}}\n\n'
                        yield "data: [DONE]\n\n"

                if stream:
                    return Response(stream_with_context(stream_response()), mimetype='text/event-stream')

                # ✅ Non-streaming mode (wait for model to finish and collect full result)
                combined_text = ""
                while True:
                    try:
                        chunk = model_stream_queue.get(timeout=15)

                        if chunk in ["END", "</s>"]:
                            logging.info("Received END signal from model backend (non-streaming).")
                            break

                        if "ttft:" in chunk or "tps:" in chunk:
                            continue

                        combined_text += chunk
                        # # 🔥 Smartly add spacing
                        # if not combined_text:
                        #     combined_text += chunk
                        # elif chunk.strip() in ['.', '?', '!', ',', ':', ';', ')']:
                        #     combined_text += chunk
                        # elif combined_text[-1] in ['(', '[', '"', "'"]:
                        #     combined_text += chunk
                        # else:
                        #     # 🔥 Normal case: add space before new chunk
                        #     combined_text += ' ' + chunk

                        model_stream_queue.task_done()

                    except queue.Empty:
                        logging.warning("Queue timeout reached in non-streaming mode.")
                        break

                # ✅ Return the full collected response
                response = {
                    "id": "chatcmpl-xyz",
                    "object": "chat.completion",
                    "model": f"{model}",
                    "choices": [{
                        "index": 0,
                        "message": {"role": "assistant", "content": combined_text},
                        "finish_reason": "stop"
                    }]
                }

                return jsonify(response)

            except Exception as e:
                logging.error(f"Error in chat_completion: {e}")
                return jsonify({'error': str(e)}), 500

        @self.app.route('/v1/chat', methods=['POST'])
        @self.app.route('/chat', methods=['POST'])
        def ollama_chat():
            try:
                data = request.get_json()
                stream = data.get('stream', False)
                messages = data.get('messages', [])
                model = data.get('model', 'model')
                options = data.get('options', {})

                user_prompt = next((msg['content'] for msg in reversed(messages) if msg['role'] == 'user'), None)

                if not user_prompt:
                    return jsonify({'error': 'No user message provided'}), 400

                logging.info(f"Received Ollama chat request: {user_prompt}")

                # ✅ Clear any ongoing generation
                self.run_stop()
                clear_model_queue()

                # ✅ Start background generation - send full conversation history
                thread = threading.Thread(target=post_to_sima, args=[messages, None, None])
                thread.start()

                # ✅ Streaming generator
                def stream_response():
                    try:
                        while True:
                            try:
                                chunk = model_stream_queue.get(timeout=15)

                                if chunk in ["END", "</s>"]:
                                    logging.info("Received END signal from model backend (streaming).")
                                    break

                                # ⚡ Format for Ollama streaming output
                                created_at = datetime.now(timezone.utc).isoformat(timespec='microseconds').replace('+00:00', 'Z')
                                stream_payload = {
                                    "model": model,
                                    "created_at": created_at,
                                    "message": {
                                        "role": "assistant",
                                        "content": chunk
                                    },
                                    "done": False
                                }
                                yield f"{json.dumps(stream_payload)}\n"

                                model_stream_queue.task_done()

                            except queue.Empty:
                                error_message = "Timeout: No response from backend."
                                yield f"{json.dumps({'error': error_message})}\n"
                                break

                    except Exception as e:
                        logging.error(f"Streaming error: {e}")
                        yield f"{json.dumps({'error': str(e)})}\n"

                    finally:
                        clear_model_queue()
                        yield f"{json.dumps({'done': True})}\n"

                if stream:
                    return Response(stream_with_context(stream_response()), mimetype='application/x-ndjson')

                # ✅ Non-streaming mode: accumulate full text
                combined_text = ""
                while True:
                    try:
                        chunk = model_stream_queue.get(timeout=15)

                        if chunk in ["END", "</s>"]:
                            logging.info("Received END signal from model backend (non-streaming).")
                            break

                        # 🔥 Smart spacing like OpenAI
                        if not combined_text:
                            combined_text += chunk
                        elif chunk.strip() in ['.', '?', '!', ',', ':', ';', ')']:
                            combined_text += chunk
                        elif combined_text[-1] in ['(', '[']:
                            combined_text += chunk
                        else:
                            combined_text += ' ' + chunk

                        model_stream_queue.task_done()

                    except queue.Empty:
                        logging.warning("Timeout in non-streaming mode.")
                        break

                # ✅ Build non-streaming response
                final_response = {
                    "model": model,
                    "created_at": "",  # Optional
                    "message": {
                        "role": "assistant",
                        "content": combined_text
                    },
                    "done": True
                }

                return jsonify(final_response)

            except Exception as e:
                logging.error(f"Error in ollama_chat: {e}")
                return jsonify({'error': str(e)}), 500
            
        @self.app.route('/upload', methods=['POST'])
        def upload():
            elapsed_time = 0.0
            mode = request.form.get('mode', 'asr').strip().lower()
            textchat = request.form.get('textchat', '').strip()
            include_chat_history = request.form.get('includeChatHistory', 'true').lower() == 'true'
            voice = request.form.get('voice', '').strip() or None
            audio_file = request.files.get('audio_data')
            audio_filename = audio_file.filename if audio_file is not None else None
            audio_mimetype = audio_file.mimetype if audio_file is not None else None

            if mode not in {'asr', 'tts', 'interleaved'}:
                return jsonify({'error': f'Invalid mode: {mode}'}), 400

            if mode in {'asr', 'interleaved'} and audio_file is None:
                return jsonify({'error': f'mode={mode} requires audio input'}), 400
            if mode == 'tts' and not textchat:
                return jsonify({'error': 'mode=tts requires text input'}), 400

            audio_bytes = None
            if audio_file is not None:
                audio_bytes = audio_file.read()
                if not audio_bytes:
                    return jsonify({'error': 'Empty audio input'}), 400

            if mode == 'tts' and textchat:
                self.add_user_message(textchat)
            stream_id = self.begin_audio_stream()
            start_time = time.time()

            if mode == 'tts':
                worker = threading.Thread(
                    target=stream_tts_to_socket,
                    args=(stream_id, textchat, voice),
                    daemon=True,
                )
            elif mode == 'interleaved':
                worker = threading.Thread(
                    target=stream_interleaved_to_socket,
                    args=(stream_id, audio_bytes, textchat, include_chat_history, voice),
                    daemon=True,
                )
            else:
                worker = threading.Thread(
                    target=stream_asr_to_socket,
                    args=(stream_id, audio_bytes),
                    daemon=True,
                )
            elapsed_time = round(time.time() - start_time, 3)
            worker.start()

            logging.info(
                "Started audio stream mode=%s history=%s stream_id=%s text='%s' voice=%s %s",
                mode,
                include_chat_history,
                stream_id,
                _preview_text(textchat),
                voice or "-",
                _audio_meta_summary(audio_bytes, audio_filename, audio_mimetype),
            )
            return {
                'question': textchat,
                'ttt': elapsed_time,
                'request_id': str(stream_id),
            }

        @self.app.route('/v1/audio/transcriptions', methods=['POST'])
        @self.app.route('/audio/transcriptions', methods=['POST'])
        def transcribe_audio():
            if 'file' not in request.files:
                return jsonify({'error': 'No audio file provided'}), 400

            audio_file = request.files['file']

            try:
                audio_bytes = audio_file.read()
                if not audio_bytes:
                    return jsonify({'error': 'Empty audio input'}), 400
                logging.info(
                    "Direct transcription request %s",
                    _audio_meta_summary(audio_bytes, audio_file.filename, audio_file.mimetype),
                )
                transcription_text = transcribe_audio_sync(audio_bytes)
                if transcription_text is not None:
                    logging.info(
                        "Direct transcription response text_chars=%s text='%s'",
                        len(transcription_text),
                        _preview_text(transcription_text),
                    )
                    return jsonify({'text': transcription_text})
                logging.error('Backend transcription failed: No valid result received')
                return jsonify({'error': 'Backend transcription failed'}), 500

            except Exception as e:
                logging.error(f"Transcription error: {e}")
                return jsonify({'error': str(e)}), 500

class HttpRequestHandler(http.server.SimpleHTTPRequestHandler):

    def log_message(self, format, *args):
        pass

    def do_POST(self):
        content_length = int(self.headers['Content-Length'])
        post_data = self.rfile.read(content_length).decode('utf-8')
        control = False
        data = None

        try:
            data = json.loads(post_data)

            # Attempt to parse 'text' field as JSON too
            try:
                inner_data = json.loads(data['text'])
                is_inner_json = True
            except (json.JSONDecodeError, TypeError):
                inner_data = None
                is_inner_json = False

            if data['text'].startswith('ttft:'):
                v = float(data['text'].split(':')[1])
                genai_app.emit('ttfs', round(v, 2))
                control = True

            elif data['text'].startswith('tps:'):
                v = float(data['text'].split(':')[1])
                genai_app.emit('tps', round(v, 2))
                control = True

            elif is_inner_json and isinstance(inner_data, dict) and 'transcription-time' in inner_data and 'text' in inner_data:
                v = float(inner_data['transcription-time'])
                genai_app.emit('transcription-time', round(v, 2))
                sanitized_tr = inner_data['text'].replace('*', '').replace('＊', '').replace('\n', '')
                genai_app.emit('transcription', sanitized_tr)
                logging.info(f"Received transcription: {sanitized_tr} (time: {v:.2f}s)")
                control = True

            else:
                text = data['text']

                # Default: treat as normal text and queue it
                sanitized_text = text.replace('*', '').replace('＊', '')
                model_stream_queue.put(sanitized_text)

            response = {"status": "Received data successfully"}
            self.send_response(200)

        except json.JSONDecodeError:
            logging.error("Invalid JSON received in standalone HTTP Server")
            response = {"status": "Invalid JSON"}
            self.send_response(400)
            self.send_header("Content-type", "application/json")
            self.end_headers()
            self.wfile.write(json.dumps(response).encode('utf-8'))
            return

        self.send_header("Content-type", "application/json")
        self.end_headers()
        self.wfile.write(json.dumps(response).encode('utf-8'))

        logging.info(f'Received response from the model backend {data}')

        if not control:
            # Filter out TPS tokens before any other processing
            text_content = data['text']

            # Track raw unsanitized response for conversation history
            if text_content.strip() == 'END':
                # Finish and save the complete assistant response to history
                genai_app.finish_assistant_response()
            elif text_content.strip() == 'FULL':
                # Context limit reached - clear backend history and notify frontend
                logging.warning("Context limit reached (FULL). Clearing conversation history.")
                genai_app.clear_conversation_history()
                genai_app.emit('context_full', {
                    'message': 'Context limit reached. History has been cleared for the next request.'
                })
            else:
                # Add raw text to current response (before any sanitization)
                genai_app.add_to_current_response(text_content)

            sanitized = text_content.replace('*', '').replace('＊', '')
            if sanitized.strip() != 'END' and sanitized.strip() != 'FULL':
                genai_app.emit('update', {"results": sanitized})
            if sanitized.strip().upper() == 'END':
                genai_app.emit('end', {})
            
class ReusableTCPServer(socketserver.TCPServer):
    allow_reuse_address = True
        
def start_http_server():
    """Start the standalone HTTP server in a separate thread."""
    cfg = genai_app.get_config()

    with ReusableTCPServer(("", cfg['SIMAAI_IP_PORT']), HttpRequestHandler) as httpd:
        logging.info(f"Standalone HTTP server is running on port {cfg['SIMAAI_IP_PORT']}")
        httpd.serve_forever()

def cleanup_data():
    logging.info('Cleaning up cached audio files')
    if os.path.exists('./uploads/audio.webm'):
        os.remove('./uploads/audio.webm')
        
def cleanup():
    shutil.rmtree('./uploads')
    os.mkdir('./uploads')


def _backend_url(path: str) -> str:
    cfg = genai_app.get_config()
    return f"http://{str(cfg['SIMAAI_IP_ADDR']).strip()}{path}"


def _preview_text(text: str | None, limit: int = 200) -> str:
    if not text:
        return ""
    normalized = text.replace("\n", "\\n")
    if len(normalized) <= limit:
        return normalized
    return normalized[: limit - 3] + "..."


def _audio_meta_summary(audio_bytes: bytes | None, filename: str | None = None, mimetype: str | None = None) -> str:
    if not audio_bytes:
        return "audio=none"
    parts = [f"bytes={len(audio_bytes)}"]
    if filename:
        parts.append(f"name={filename}")
    if mimetype:
        parts.append(f"type={mimetype}")
    return "audio(" + ", ".join(parts) + ")"


def _emit_audio_end_once(stream_id: int, end_state: dict[str, bool]):
    if not end_state["sent"] and genai_app.is_audio_stream_active(stream_id):
        genai_app.emit('end', {})
        end_state["sent"] = True


def _post_sse(path: str, *, files=None, data=None, json_payload=None) -> requests.Response:
    kwargs: dict[str, Any] = {
        "stream": True,
        "timeout": (10, 300),
    }
    if files is not None:
        kwargs["files"] = files
    if data is not None:
        kwargs["data"] = data
    if json_payload is not None:
        kwargs["json"] = json_payload
    return requests.post(_backend_url(path), **kwargs)


def _handle_audio_stream_error(stream_id: int, mode: str, exc: Exception, end_state: dict[str, bool] | None = None):
    logging.error("Audio stream failed for mode=%s stream_id=%s: %s", mode, stream_id, exc)
    if not genai_app.is_audio_stream_active(stream_id):
        return

    message = _sanitize_stream_text(str(exc))
    if mode == "asr":
        genai_app.emit('transcription_chunk', {'text': message, 'delta': message, 'error': True})
    else:
        genai_app.emit('update', {'results': message})
        if end_state is not None:
            _emit_audio_end_once(stream_id, end_state)


def transcribe_audio_sync(audio_bytes: bytes) -> str | None:
    with _post_sse(
        "/v1/audio/transcriptions",
        files={"file": ("audio.webm", audio_bytes, "audio/webm")},
    ) as response:
        response.raise_for_status()
        text_parts: list[str] = []
        request_id = None
        for event_name, payload in _iter_sse_events(response):
            if not isinstance(payload, dict):
                continue
            if event_name == "run_started":
                request_id = payload.get("request_id")
            elif event_name == "text_chunk":
                text = payload.get("text")
                if isinstance(text, str) and text:
                    text_parts.append(text)
        full_text = "".join(text_parts) or None
        logging.info(
            "Direct transcription SSE summary request_id=%s text_chunks=%s text_chars=%s text='%s'",
            request_id or "-",
            len(text_parts),
            len(full_text or ""),
            _preview_text(full_text),
        )
        return full_text


def stream_asr_to_socket(stream_id: int, audio_bytes: bytes):
    start_time = time.time()
    text_parts: list[str] = []
    emitted_first_text = False
    request_id = None

    try:
        with _post_sse(
            "/v1/audio/transcriptions",
            files={"file": ("audio.webm", audio_bytes, "audio/webm")},
        ) as response:
            response.raise_for_status()
            for event_name, payload in _iter_sse_events(response):
                if not genai_app.is_audio_stream_active(stream_id):
                    return
                if not isinstance(payload, dict):
                    continue
                if event_name == "run_started":
                    request_id = payload.get("request_id")
                    logging.info("ASR stream connected stream_id=%s request_id=%s", stream_id, request_id or "-")
                elif event_name == "text_chunk":
                    text = payload.get("text")
                    if not isinstance(text, str) or not text:
                        continue
                    text_parts.append(text)
                    if not emitted_first_text:
                        genai_app.emit('ttfs', round(time.time() - start_time, 2))
                        emitted_first_text = True
                    genai_app.emit('transcription_chunk', {
                        'text': "".join(text_parts),
                        'delta': _sanitize_stream_text(text),
                    })
                elif event_name == "run_error":
                    raise RuntimeError(payload.get("meta", {}).get("error", "ASR request failed"))
    except Exception as exc:
        _handle_audio_stream_error(stream_id, "asr", exc)
        return

    if not genai_app.is_audio_stream_active(stream_id):
        return

    full_text = _sanitize_stream_text("".join(text_parts))
    elapsed_sec = time.time() - start_time
    genai_app.emit('transcription-time', round(time.time() - start_time, 2))
    if full_text:
        genai_app.emit('transcription', full_text)
    logging.info(
        "ASR stream summary stream_id=%s request_id=%s text_chunks=%s text_chars=%s elapsed=%.3fs text='%s'",
        stream_id,
        request_id or "-",
        len(text_parts),
        len(full_text),
        elapsed_sec,
        _preview_text(full_text),
    )


def stream_tts_to_socket(stream_id: int, text: str, voice: str | None):
    end_state = {"sent": False}
    start_time = time.time()
    request_id = None
    audio_chunk_count = 0
    total_audio_duration_ms = 0.0
    sample_rate_hz = None
    stop_reason = None

    try:
        with _post_sse(
            "/v1/audio/speech",
            json_payload={"input": text, "voice": voice},
        ) as response:
            response.raise_for_status()
            for event_name, payload in _iter_sse_events(response):
                if not genai_app.is_audio_stream_active(stream_id):
                    return
                if not isinstance(payload, dict):
                    continue
                if event_name == "run_started":
                    request_id = payload.get("request_id")
                    logging.info("TTS stream connected stream_id=%s request_id=%s", stream_id, request_id or "-")
                elif event_name == "audio_chunk":
                    audio_chunk_count += 1
                    total_audio_duration_ms += float(payload.get('duration_ms', 0.0))
                    sample_rate_hz = int(payload.get('sample_rate_hz', sample_rate_hz or 24000))
                    genai_app.emit('audio_chunk', {
                        'text': payload.get('text', ''),
                        'audio_pcm_i16_b64': payload.get('audio_pcm_i16_b64', ''),
                        'sample_rate_hz': sample_rate_hz,
                        'rtf': payload.get('rtf', 0),
                        'tps': payload.get('tps', 0),
                    })
                elif event_name == "audio_end":
                    stop_reason = payload.get("meta", {}).get("stop_reason")
                    _emit_audio_end_once(stream_id, end_state)
                elif event_name in {"audio_end", "run_finished"}:
                    _emit_audio_end_once(stream_id, end_state)
                elif event_name == "run_error":
                    raise RuntimeError(payload.get("meta", {}).get("error", "TTS request failed"))
    except Exception as exc:
        _handle_audio_stream_error(stream_id, "tts", exc, end_state)
        return

    logging.info(
        "TTS stream summary stream_id=%s request_id=%s voice=%s text='%s' audio_chunks=%s audio_duration_ms=%.1f sample_rate_hz=%s stop_reason=%s elapsed=%.3fs",
        stream_id,
        request_id or "-",
        voice or "-",
        _preview_text(text),
        audio_chunk_count,
        total_audio_duration_ms,
        sample_rate_hz or "-",
        stop_reason or "-",
        time.time() - start_time,
    )


def stream_interleaved_to_socket(
    stream_id: int,
    audio_bytes: bytes,
    text: str,
    include_chat_history: bool,
    voice: str | None,
):
    end_state = {"sent": False}
    emitted_first_text = False
    start_time = time.time()
    request_id = None
    text_parts: list[str] = []
    audio_chunk_count = 0
    total_audio_duration_ms = 0.0
    sample_rate_hz = None
    stop_reason = None

    data: dict[str, str] = {
        "include_chat_history": "true" if include_chat_history else "false",
    }
    if text:
        data["text"] = text
    if voice:
        data["voice"] = voice

    try:
        with _post_sse(
            "/v1/realtime",
            files={"file": ("audio.webm", audio_bytes, "audio/webm")},
            data=data,
        ) as response:
            response.raise_for_status()
            for event_name, payload in _iter_sse_events(response):
                if not genai_app.is_audio_stream_active(stream_id):
                    return
                if not isinstance(payload, dict):
                    continue
                if event_name == "run_started":
                    request_id = payload.get("request_id")
                    logging.info("Interleaved stream connected stream_id=%s request_id=%s", stream_id, request_id or "-")
                elif event_name == "text_chunk":
                    text_chunk = payload.get("text")
                    if not isinstance(text_chunk, str) or not text_chunk:
                        continue
                    text_parts.append(text_chunk)
                    if not emitted_first_text:
                        genai_app.emit('ttfs', round(time.time() - start_time, 2))
                        emitted_first_text = True
                    genai_app.emit('update', {"results": _sanitize_stream_text(text_chunk)})
                elif event_name == "audio_chunk":
                    audio_chunk_count += 1
                    total_audio_duration_ms += float(payload.get('duration_ms', 0.0))
                    sample_rate_hz = int(payload.get('sample_rate_hz', sample_rate_hz or 24000))
                    genai_app.emit('audio_chunk', {
                        'text': payload.get('text', ''),
                        'audio_pcm_i16_b64': payload.get('audio_pcm_i16_b64', ''),
                        'sample_rate_hz': sample_rate_hz,
                        'rtf': payload.get('rtf', 0),
                        'tps': payload.get('tps', 0),
                    })
                elif event_name == "audio_end":
                    stop_reason = payload.get("meta", {}).get("stop_reason")
                    _emit_audio_end_once(stream_id, end_state)
                elif event_name in {"audio_end", "run_finished"}:
                    _emit_audio_end_once(stream_id, end_state)
                elif event_name == "run_error":
                    raise RuntimeError(payload.get("meta", {}).get("error", "Realtime request failed"))
    except Exception as exc:
        _handle_audio_stream_error(stream_id, "interleaved", exc, end_state)
        return

    full_text = _sanitize_stream_text("".join(text_parts))
    logging.info(
        "Interleaved stream summary stream_id=%s request_id=%s history=%s text_in='%s' voice=%s text_chunks=%s text='%s' audio_chunks=%s audio_duration_ms=%.1f sample_rate_hz=%s stop_reason=%s elapsed=%.3fs",
        stream_id,
        request_id or "-",
        include_chat_history,
        _preview_text(text),
        voice or "-",
        len(text_parts),
        _preview_text(full_text),
        audio_chunk_count,
        total_audio_duration_ms,
        sample_rate_hz or "-",
        stop_reason or "-",
        time.time() - start_time,
    )

# Function to post the file to another server
def post_to_sima(conversation_history, image_path=None, audio_path=None, language='en', search_rag=False):
    genai_app.emit('update', {"progress": "SiMa.ai is processing, please wait..."})
    cfg = genai_app.get_config()
    url = 'http://' + str(cfg['SIMAAI_IP_ADDR']).strip()

    base64_audio = None

    # Note: Images are now embedded in conversation_history, no need for separate image processing

    # Convert audio to base64 if present.
    if audio_path is not None:
        try:
            with open(audio_path, 'rb') as audio_file:
                base64_audio = base64.b64encode(audio_file.read()).decode('utf-8')
        except Exception as e:
            logging.warning(f"Failed to read audio {audio_path}: {e}")

    # Post request to SIMA server with OpenAI-style conversation history
    payload = {
        'text': conversation_history,  # Send full conversation history array (images embedded)
        'language': language,
        'audio': base64_audio,
        'search_rag': search_rag
    }
    logging.debug(f'Posting to SIMA LLaMA server at {url} with payload: {payload}')

    try:
        response = requests.post(url, json=payload, timeout=15)
        logging.info(f'HTTP Status: {response.status_code}')

        if response.status_code == 200:
            try:
                logging.info('Sima model server responded OK')
            except requests.exceptions.JSONDecodeError:
                logging.warning("Response content is not valid JSON.")
                logging.info(f"Raw response content: {response.text}")
        else:
            logging.warning(f"Received error response from SIMA server: {response.status_code}")
            logging.info(f"Raw error response: {response.text}")

    except requests.exceptions.RequestException as e:
        logging.error(f"Request to SIMA server failed: {e}")

    # ✅ Always clean up temporary data
    cleanup_data()

def post_stop_to_sima():
    cfg = genai_app.get_config()
    url = f"http://{str(cfg['SIMAAI_IP_ADDR']).strip()}/stop"

    logging.debug(f'Posting STOP signal to SIMA model server at {url}')
    last_err = None
    for attempt in range(1, 4):
        try:
            response = requests.post(url, timeout=5)
            response.raise_for_status()
            logging.info("Successfully sent stop signal to SiMa.ai server (attempt %d).", attempt)
            return True
        except requests.RequestException as e:
            last_err = e
            logging.warning("Stop signal attempt %d failed: %s", attempt, e)
            time.sleep(0.15)
    logging.error(f"Failed to send stop signal after retries: {last_err}")
    return False


def post_reset_conversation_to_mla():
    cfg = genai_app.get_config()
    url = f"http://{str(cfg['SIMAAI_IP_ADDR']).strip()}/reset_conversation"
    headers = {'Content-Type': 'application/json'}

    logging.debug(f'Posting reset conversation request to SIMA model server at {url}')
    try:
        response = requests.post(url, json={}, headers=headers, timeout=10)
        response.raise_for_status()
        logging.info("Successfully reset backend conversation state.")
        return True
    except requests.RequestException as e:
        logging.error(f"Failed to reset backend conversation state: {e}")
        return False

if __name__ == '__main__':
    log_filename = 'server.log'
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
        handlers=[
            logging.FileHandler(log_filename, mode='w', encoding='utf-8'),
            logging.StreamHandler(sys.stdout)
        ]
    )
    logging.info('Initializing genai-demo app (frontend and TTS) please wait....')
    genai_app = AppContext()
    genai_app.initialize()
    
    parser = argparse.ArgumentParser(description ='SiMa.ai GenAI demo application args')
    parser.add_argument('--camidx', type=int, required=False)
    parser.add_argument('--ip', type=str, required=False)
    parser.add_argument('--ragserver', type=str, required=False)
    parser.add_argument('--httponly', action='store_true', help='Run app server in http only')
    parser.add_argument('--system-prompt-file', type=str, required=False, help='Provide a text file containing custom system prompt')
    parser.add_argument('--model-config', type=str, required=False, help='Path to model vlm_config.json')

    args = parser.parse_args()
    config_model_name = None
    vision_image_size_arg = None

    if args.model_config:
        try:
            with open(args.model_config, 'r', encoding='utf-8') as fh:
                cfg = json.load(fh)
            config_model_name = cfg.get('model_name')

            if isinstance(config_model_name, str):
                config_model_name = config_model_name.strip() or None
            else:
                config_model_name = None
            raw_size = cfg.get('vm_cfg', {}).get('image_size')
            if raw_size is not None:
                if isinstance(raw_size, int):
                    vision_image_size_arg = f"{raw_size}x{raw_size}"
                elif isinstance(raw_size, (list, tuple)) and len(raw_size) == 2:
                    vision_image_size_arg = f"{raw_size[0]}x{raw_size[1]}"
        except Exception as exc:
            logging.warning(f"Failed to read model config '{args.model_config}': {exc}")

    vision_image_size = parse_vision_image_size(vision_image_size_arg)
    genai_app.update_settings(
        args.camidx,
        args.ip,
        args.ragserver,
        args.httponly,
        config_model_name,
        vision_image_size
    )

    if args.system_prompt_file:
        prompt_path = Path(args.system_prompt_file).expanduser()
        try:
            prompt_text = prompt_path.read_text(encoding='utf-8')
            genai_app.set_system_prompt(prompt_text)
        except Exception as exc:
            logging.error(f"Failed to load system prompt file '{prompt_path}': {exc}")

    # Setup routes after settings are configured
    genai_app.setup_router()
    cleanup()
    
    logging.info(f'Connecting to SiMa.ai genai server with {args.camidx} {args.ip}')
    server_thread = threading.Thread(target=start_http_server)
    server_thread.daemon = True
    server_thread.start()
    logging.info("Started standalone HTTP server in a separate thread")
    genai_app.run()
