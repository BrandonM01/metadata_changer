from dotenv import load_dotenv
import os

# Load environment variables from .env
load_dotenv()

from flask import (
    Flask, render_template, request, redirect,
    url_for, flash, session, send_file,
    send_from_directory, jsonify
)
from flask_sqlalchemy import SQLAlchemy
from flask_migrate import Migrate
from flask_login import (
    LoginManager, login_user, logout_user,
    login_required, UserMixin, current_user
)
from werkzeug.security import generate_password_hash, check_password_hash
from pydrive.auth import GoogleAuth
from pydrive.drive import GoogleDrive
from PIL import Image, ImageEnhance
import random
import zipfile
import shutil
import datetime
import os
import ffmpeg

# Import billing blueprints
from billing import subscription_bp, referral_bp

# -------------------- App & DB Setup --------------------
app = Flask(__name__)
app.secret_key = os.getenv('FLASK_SECRET_KEY', 'please_change_me')
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///users.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

db = SQLAlchemy(app)
migrate = Migrate(app, db)

# -------------------- Login Manager --------------------
login_manager = LoginManager()
login_manager.login_view = 'login'
login_manager.init_app(app)

# -------------------- Folders --------------------
for folder in ('uploads', 'processed', 'static/history', 'static/processed_zips'):
    os.makedirs(folder, exist_ok=True)

# -------------------- Models --------------------
class User(UserMixin, db.Model):
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(150), unique=True, nullable=False)
    password = db.Column(db.String(150), nullable=False)
    username = db.Column(db.String(150), default='New User')
    backup_enabled = db.Column(db.Boolean, default=False)
    dark_mode_enabled = db.Column(db.Boolean, default=False)

    # Subscription & Billing
    stripe_customer_id     = db.Column(db.String(100), nullable=True)
    stripe_subscription_id = db.Column(db.String(100), nullable=True)
    plan                   = db.Column(db.String(50), default='free')
    tokens                 = db.Column(db.Integer, default=0)

    # Referral
    referral_code   = db.Column(db.String(20), unique=True, nullable=True)
    referred_by_id  = db.Column(db.Integer, db.ForeignKey('user.id'), nullable=True)
    referrals       = db.relationship(
        'User', backref=db.backref('referrer', remote_side=[id]), lazy='dynamic'
    )

@login_manager.user_loader
def load_user(user_id):
    return db.session.get(User, int(user_id))

# -------------------- Auth & Referral --------------------
@app.route('/apply-referral/<code>')
def apply_referral(code):
    session['referral_code'] = code
    return redirect(url_for('register'))

@app.route('/register', methods=['GET', 'POST'])
def register():
    if request.method == 'POST':
        email, password = request.form['email'], request.form['password']
        if User.query.filter_by(email=email).first():
            flash('⚠️ Email already registered.', 'error')
            return redirect(url_for('register'))
        new_user = User(
            email=email,
            password=generate_password_hash(password),
            username=email.split('@')[0]
        )
        # referral bonus on registration
        code = session.pop('referral_code', None)
        if code:
            ref = User.query.filter_by(referral_code=code).first()
            if ref and ref.id != new_user.id:
                new_user.referred_by_id = ref.id
                ref.tokens += 10
                db.session.add(ref)
        # generate referral code
        new_user.referral_code = ''.join(
            random.choices(random.choices.__defaults__[0] + random.choices.__defaults__[1], k=8)
        )
        db.session.add(new_user)
        db.session.commit()
        flash('✅ Registration successful! Please log in.', 'success')
        return redirect(url_for('login'))
    return render_template('register.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email, password = request.form['email'], request.form['password']
        user = User.query.filter_by(email=email).first()
        if user and check_password_hash(user.password, password):
            login_user(user)
            return redirect(url_for('home'))
        flash('❌ Login failed. Check your credentials.', 'error')
    return render_template('login.html')

@app.route('/logout')
@login_required
def logout():
    logout_user()
    flash('👋 Logged out successfully.', 'success')
    return redirect(url_for('login'))

# -------------------- Settings --------------------
@app.route('/settings', methods=['GET', 'POST'])
@login_required
def settings():
    if request.method == 'POST':
        current_user.username = request.form.get('username', current_user.username)
        current_user.backup_enabled = 'backup_enabled' in request.form
        current_user.dark_mode_enabled = 'dark_mode_enabled' in request.form
        db.session.commit()
        flash('✅ Settings updated.', 'success')
        return redirect(url_for('settings'))
    referral_link = url_for('apply_referral', code=current_user.referral_code, _external=True)
    return render_template('settings.html', referral_link=referral_link)

# -------------------- Plans Page --------------------
@app.route('/plans')
@login_required
def plans():
    key = os.getenv('STRIPE_PUBLISHABLE_KEY')
    return render_template('plans.html', stripe_publishable_key=key)

# -------------------- Stripe Key API --------------------
@app.route('/stripe-key')
@login_required
def stripe_key():
    return jsonify({'publishableKey': os.getenv('STRIPE_PUBLISHABLE_KEY')})

# -------------------- UI Pages --------------------
@app.route('/')
@login_required
def home():
    return render_template('home.html')

@app.route('/image-processor')
@login_required
def image_processor():
    return render_template('image_processor.html')

@app.route('/video-processor')
@login_required
def video_processor():
    return render_template('video_processor.html')

# -------------------- History --------------------
@app.route('/history')
@login_required
def history():
    page = int(request.args.get('page', 1))
    per_page = 25
    history_folder = os.path.join('static', 'history')
    all_files = sorted(
        os.listdir(history_folder),
        key=lambda x: os.path.getmtime(os.path.join(history_folder, x)),
        reverse=True
    )
    total_pages = (len(all_files) + per_page - 1) // per_page
    files = all_files[(page-1)*per_page:page*per_page]
    return render_template('history.html', files=files, page=page, total_pages=total_pages)

@app.route('/download/<filename>')
@login_required
def download_file(filename):
    return send_from_directory('static/history', filename, as_attachment=True)

@app.route('/download-zip/<filename>')
@login_required
def download_zip(filename):
    return send_from_directory('static/processed_zips', filename, as_attachment=True)

# -------------------- Google Drive Backup --------------------
def upload_to_google_drive(file_path, filename):
    gauth = GoogleAuth()
    gauth.LoadCredentialsFile("credentials.json")
    if gauth.credentials is None:
        gauth.LocalWebserverAuth()
    elif gauth.access_token_expired:
        gauth.Refresh()
    else:
        gauth.Authorize()
    gauth.SaveCredentialsFile("credentials.json")

    drive = GoogleDrive(gauth)
    folder_list = drive.ListFile({'q': "title='MetadataChangerBackup' and mimeType='application/vnd.google-apps.folder' and trashed=false"}).GetList()
    if folder_list:
        folder_id = folder_list[0]['id']
    else:
        folder = drive.CreateFile({'title': 'MetadataChangerBackup', 'mimeType': 'application/vnd.google-apps.folder'})
        folder.Upload()
        folder_id = folder['id']
    file_drive = drive.CreateFile({'title': filename, 'parents': [{'id': folder_id}]})
    file_drive.SetContentFile(file_path)
    file_drive.Upload()

# -------------------- Helpers --------------------
def scale_range(min_val, max_val, intensity):
    factor = intensity / 100
    return random.uniform(min_val*factor, max_val*factor)

# -------------------- Process Images --------------------
@app.route('/process-images', methods=['POST'])
@login_required
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

    for image_file in images:
        img = Image.open(image_file)
        base = os.path.splitext(image_file.filename)[0]
        for i in range(batch_size):
            var = img.copy()
            if adjust_contrast:
                var = ImageEnhance.Contrast(var).enhance(1 + scale_range(-0.1,0.1,intensity))
            if adjust_brightness:
                var = ImageEnhance.Brightness(var).enhance(1 + scale_range(-0.1,0.1,intensity))
            if rotate:
                var = var.rotate(scale_range(-5,5,intensity), expand=True)
            if crop:
                w,h = var.size
                dx,dy = int(w*scale_range(0.01,0.05,intensity)), int(h*scale_range(0.01,0.05,intensity))
                var = var.crop((dx,dy,w-dx,h-dy))
            if flip_horizontal and random.random()>0.5:
                var = var.transpose(Image.FLIP_LEFT_RIGHT)
            fname = f"{base}_variant_{i+1}.jpg"
            var.save(os.path.join(output_folder, fname))
            var.save(os.path.join('static/history', fname))

    zip_fn = f"images_{timestamp}.zip"
    zip_path = os.path.join('static/processed_zips', zip_fn)
    with zipfile.ZipFile(zip_path,'w') as zf:
        for f in os.listdir(output_folder): zf.write(os.path.join(output_folder,f), arcname=f)
    shutil.rmtree(output_folder)
    if current_user.backup_enabled:
        upload_to_google_drive(zip_path, zip_fn)
    return jsonify({'zip_filename': zip_fn})

# -------------------- Process Videos --------------------
@app.route('/process-videos', methods=['POST'])
@login_required
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

    for video_file in videos:
        src = os.path.join('uploads', video_file.filename)
        video_file.save(src)
        probe = ffmpeg.probe(src)
        v_stream = next(s for s in probe['streams'] if s['codec_type']=='video')
        w,h = int(v_stream['width']), int(v_stream['height'])
        base = os.path.splitext(video_file.filename)[0]
        for i in range(batch_size):
            outp = os.path.join(output_folder, f"{base}_variant_{i+1}.mp4")
            hist = os.path.join('static/history', f"{base}_variant_{i+1}.mp4")
            st = ffmpeg.input(src)
            if adjust_contrast or adjust_brightness:
                c = 1+scale_range(-0.1,0.1,intensity) if adjust_contrast else 1
                b = scale_range(-0.05,0.05,intensity) if adjust_brightness else 0
                st = st.filter('eq',contrast=c,brightness=b)
            if rotate:
                st = st.filter('rotate', scale_range(-2,2,intensity)*3.1415/180)
            if crop:
                dx,dy = int(w*scale_range(0.01,0.03,intensity)), int(h*scale_range(0.01,0.03,intensity))
                st = st.filter('crop', w-2*dx, h-2*dy, dx, dy).filter('scale', w, h)
            if flip_horizontal and random.random()>0.5:
                st = st.filter('hflip')
            stream = ffmpeg.output(st, outp, vcodec='libx264', acodec='aac')
            ffmpeg.run(stream, overwrite_output=True)
            shutil.copy(outp, hist)
        os.remove(src)
    zip_fn = f"videos_{timestamp}.zip"
    zip_path = os.path.join('static/processed_zips', zip_fn)
    with zipfile.ZipFile(zip_path,'w') as zf:
        for f in os.listdir(output_folder): zf.write(os.path.join(output_folder,f), arcname=f)
    shutil.rmtree(output_folder)
    if current_user.backup_enabled:
        upload_to_google_drive(zip_path, zip_fn)
    return jsonify({'zip_filename': zip_fn})

# -------------------- Blueprints --------------------
app.register_blueprint(subscription_bp, url_prefix='/subscription')
app.register_blueprint(referral_bp, url_prefix='/referral')

if __name__ == '__main__':
    app.run(debug=True)