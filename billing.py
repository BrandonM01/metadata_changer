from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

import stripe
from flask import Blueprint, request, jsonify, url_for
from flask_login import login_required, current_user

stripe.api_key = os.getenv('STRIPE_SECRET_KEY')

subscription_bp = Blueprint('subscription', __name__)
referral_bp     = Blueprint('referral', __name__)

def get_models():
    from app import db, User
    return db, User

@subscription_bp.route('/create-checkout-session', methods=['POST'])
@login_required
def create_checkout():
    db, User = get_models()
    data = request.get_json() or {}
    plan_id = data.get('plan')
    if not current_user.stripe_customer_id:
        cust = stripe.Customer.create(email=current_user.email)
        current_user.stripe_customer_id = cust.id
        db.session.commit()
    session = stripe.checkout.Session.create(
        customer=current_user.stripe_customer_id,
        payment_method_types=['card'],
        line_items=[{'price': plan_id, 'quantity': 1}],
        mode='subscription',
        success_url=url_for('subscription.success', _external=True) + '?session_id={CHECKOUT_SESSION_ID}',
        cancel_url=url_for('settings', _external=True)
    )
    return jsonify({'sessionId': session.id})

@subscription_bp.route('/webhook', methods=['POST'])
def webhook_received():
    db, User = get_models()
    payload = request.data
    sig = request.headers.get('stripe-signature')
    try:
        event = stripe.Webhook.construct_event(payload, sig, os.getenv('STRIPE_WEBHOOK_SECRET'))
    except Exception as e:
        return jsonify({'error': str(e)}), 400
    if event['type'] == 'invoice.payment_succeeded':
        inv = event['data']['object']
        user = User.query.filter_by(stripe_subscription_id=inv['subscription']).first()
        if user:
            user.tokens += 100
            db.session.commit()
    if event['type'] == 'customer.subscription.created':
        sub = event['data']['object']
        user = User.query.filter_by(stripe_customer_id=sub['customer']).first()
        if user:
            user.plan = sub['items']['data'][0]['price']['nickname']
            user.stripe_subscription_id = sub['id']
            db.session.commit()
    return jsonify({'status': 'success'})