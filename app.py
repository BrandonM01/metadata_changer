# imports
from flask import Flask, render_template, request, send_file, jsonify, send_from_directory
from PIL import Image, ImageEnhance
import random
import zipfile
import io
import shutil
import datetime
import os
import ffmpeg
import time

app = Flask(__name__)

# create folders if they don't exist
os.makedirs('uploads', exist_ok=True)
os.makedirs('processed', exist_ok=True)
os.makedirs('static/history', exist_ok=True)
os.makedirs('static/processed_zips', exist_ok=True)

# page routes
@app.route('/')
def home():
    return render_template('home.html')

@app.route('/image-processor')
def image_processor():
    return render_template('image_processor.html')

@app.route('/video-processor')
def video_processor():
    return render_template('video_processor.html')

# Updated History page with Pagination
@app.route('/history')
def history():
    page = request.args.get('page', 1, type=int)
    per_page = 24  # Files per page

    history_folder = os.path.join('static', 'history')
    all_files = sorted(os.listdir(history_folder), key=lambda x: os.path.getmtime(os.path.join(history_folder, x)), reverse=True)

    total_files = len(all_files)
    total_pages = (total_files + per_page - 1) // per_page

    start = (page - 1) * per_page
    end = start + per_page
    files = all_files[start:end]

    return render_template('history.html', files=files, page=page, total_pages=total_pages)

# Download a single file
@app.route('/download/<filename>')
def download_file(filename):
    return send_from_directory('static/history', filename, as_attachment=True)

# Download generated zip
@app.route('/download-zip/<filename>')
def download_zip(filename):
    return send_from_directory('static/processed_zips', filename, as_attachment=True)

# Delete a single file
@app.route('/delete-file', methods=['POST'])
def delete_file():
    data = request.get_json()
    filename = data.get('filename')
    if filename:
        file_path = os.path.join('static/history', filename)
        if os.path.exists(file_path):
            os.remove(file_path)
            return jsonify({'success': True})
    return jsonify({'success': False})

# Delete multiple files
@app.route('/delete-multiple', methods=['POST'])
def delete_multiple():
    data = request.get_json()
    files = data.get('files', [])
    for filename in files:
        file_path = os.path.join('static/history', filename)
        if os.path.exists(file_path):
            os.remove(file_path)
    return jsonify({'success': True})

# Download multiple files as ZIP
@app.route('/download-multiple', methods=['POST'])
def download_multiple():
    data = request.get_json()
    files = data.get('files', [])
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, 'w') as zipf:
        for filename in files:
            file_path = os.path.join('static/history', filename)
            if os.path.exists(file_path):
                zipf.write(file_path, arcname=filename)
    zip_buffer.seek(0)
    return send_file(zip_buffer, mimetype='application/zip', download_name='selected_files.zip', as_attachment=True)

# get history files (auto-clean older than 7 days)
@app.route('/get-history')
def get_history():
    history_folder = os.path.join('static', 'history')
    files = []
    now = time.time()
    cutoff = 7 * 24 * 60 * 60  # 7 days

    for filename in os.listdir(history_folder):
        file_path = os.path.join(history_folder, filename)
        if os.path.isfile(file_path):
            file_age = now - os.path.getmtime(file_path)
            if file_age > cutoff:
                os.remove(file_path)
            else:
                files.append(filename)

    return jsonify(files)

# Process Images
@app.route('/process-images', methods=['POST'])
def process_images():
    images = request.files.getlist('images')
    batch_size = int(request.form.get('batch_size', 5))
    intensity = int(request.form.get('intensity', 30))

    adjust_contrast = 'adjust_contrast' in request.form
    adjust_brightness = 'adjust_brightness' in request.form
    rotate = 'rotate' in request.form
    crop = 'crop' in request.form
    flip_horizontal = 'flip_horizontal' in request.form

    timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    output_folder = os.path.join('processed', timestamp)
    os.makedirs(output_folder, exist_ok=True)

    def scale_range(min_val, max_val):
        factor = intensity / 100
        return random.uniform(min_val * factor, max_val * factor)

    for image_file in images:
        img = Image.open(image_file)
        filename = os.path.splitext(image_file.filename)[0]

        for i in range(batch_size):
            variant = img.copy()

            if adjust_contrast:
                enhancer = ImageEnhance.Contrast(variant)
                factor = 1 + scale_range(-0.1, 0.1)
                variant = enhancer.enhance(factor)

            if adjust_brightness:
                enhancer = ImageEnhance.Brightness(variant)
                factor = 1 + scale_range(-0.1, 0.1)
                variant = enhancer.enhance(factor)

            if rotate:
                angle = scale_range(-5, 5)
                variant = variant.rotate(angle, expand=True)

            if crop:
                width, height = variant.size
                crop_x = int(width * scale_range(0.01, 0.05))
                crop_y = int(height * scale_range(0.01, 0.05))
                variant = variant.crop((crop_x, crop_y, width - crop_x, height - crop_y))

            if flip_horizontal and random.random() > 0.5:
                variant = variant.transpose(Image.FLIP_LEFT_RIGHT)

            output_path = os.path.join(output_folder, f"{filename}_variant_{i+1}.jpg")
            variant.save(output_path, quality=95)

            # Save to history
            history_path = os.path.join('static/history', f"{filename}_variant_{i+1}.jpg")
            variant.save(history_path, quality=95)

    # Create zip
    zip_filename = f"images_{timestamp}.zip"
    zip_path = os.path.join('static/processed_zips', zip_filename)

    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for root, dirs, files in os.walk(output_folder):
            for file in files:
                file_path = os.path.join(root, file)
                zipf.write(file_path, arcname=file)

    shutil.rmtree(output_folder)

    return jsonify({'zip_filename': zip_filename})

# Process Videos
@app.route('/process-videos', methods=['POST'])
def process_videos():
    videos = request.files.getlist('videos')
    batch_size = int(request.form.get('batch_size', 5))
    intensity = int(request.form.get('intensity', 30))

    adjust_contrast = 'adjust_contrast' in request.form
    adjust_brightness = 'adjust_brightness' in request.form
    rotate = 'rotate' in request.form
    crop = 'crop' in request.form
    flip_horizontal = 'flip_horizontal' in request.form

    timestamp = datetime.datetime.now().strftime('%Y%m%d%H%M%S')
    output_folder = os.path.join('processed', timestamp)
    os.makedirs(output_folder, exist_ok=True)

    def scale_range(min_val, max_val):
        factor = intensity / 100
        return random.uniform(min_val * factor, max_val * factor)

    for video_file in videos:
        video_path = os.path.join('uploads', video_file.filename)
        video_file.save(video_path)
        filename = os.path.splitext(video_file.filename)[0]

        probe = ffmpeg.probe(video_path)
        video_stream = next(stream for stream in probe['streams'] if stream['codec_type'] == 'video')
        original_width = int(video_stream['width'])
        original_height = int(video_stream['height'])

        if batch_size <= 5:
            crop_min = 0.005
            crop_max = 0.015
        elif batch_size <= 10:
            crop_min = 0.01
            crop_max = 0.02
        else:
            crop_min = 0.015
            crop_max = 0.03

        for i in range(batch_size):
            output_path = os.path.join(output_folder, f"{filename}_variant_{i+1}.mp4")
            history_path = os.path.join('static/history', f"{filename}_variant_{i+1}.mp4")

            stream = ffmpeg.input(video_path)

            if adjust_contrast or adjust_brightness:
                contrast = 1 + scale_range(-0.1, 0.1) if adjust_contrast else 1
                brightness = scale_range(-0.05, 0.05) if adjust_brightness else 0
                stream = stream.filter('eq', contrast=contrast, brightness=brightness)

            if rotate:
                angle = scale_range(-2, 2)
                radians = angle * (3.14159265 / 180)
                stream = stream.filter('rotate', radians)

            if crop:
                crop_pixels_x = int(original_width * scale_range(crop_min, crop_max))
                crop_pixels_y = int(original_height * scale_range(crop_min, crop_max))
                crop_w = original_width - crop_pixels_x * 2
                crop_h = original_height - crop_pixels_y * 2
                stream = stream.filter('crop', crop_w, crop_h, crop_pixels_x, crop_pixels_y)
                stream = stream.filter('scale', original_width, original_height)

            if flip_horizontal and random.random() > 0.5:
                stream = stream.filter('hflip')

            stream = ffmpeg.output(stream, output_path, vcodec='libx264', acodec='aac', strict='experimental')
            ffmpeg.run(stream, overwrite_output=True)

            shutil.copy(output_path, history_path)

        os.remove(video_path)

    # Create zip
    zip_filename = f"videos_{timestamp}.zip"
    zip_path = os.path.join('static/processed_zips', zip_filename)

    with zipfile.ZipFile(zip_path, 'w') as zipf:
        for root, dirs, files in os.walk(output_folder):
            for file in files:
                file_path = os.path.join(root, file)
                zipf.write(file_path, arcname=file)

    shutil.rmtree(output_folder)

    return jsonify({'zip_filename': zip_filename})

if __name__ == '__main__':
    app.run(debug=True)
