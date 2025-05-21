from flask import Flask, request, jsonify, render_template
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
import firebase_admin
from firebase_admin import credentials, firestore
import uuid
from datetime import datetime
from flask_cors import CORS
import time
import threading
import signal
import sys
import os

app = Flask(__name__)
# Enable CORS for all routes and all origins
CORS(app, resources={r"/*": {"origins": "*"}})

# Set the base URL for the backend server
BACKEND_URL = "https://lost-found-backend-fchf.onrender.com"

# Hardcoded configuration values to avoid environment variables
EMAIL_USER = "lostandfound.vitb@gmail.com"
EMAIL_PASSWORD = "cycv npro qenf molb"
APP_VERSION = "1.0.0"

# Initialize Firebase
try:
    # Always use the same path for Firebase credentials
    cred_path = "lostandfound-01-firebase-adminsdk-fbsvc-9586ed19d6.json"
    cred = credentials.Certificate(cred_path)
    firebase_admin.initialize_app(cred, {
        'databaseURL': 'https://lostandfound-01-default-rtdb.asia-southeast1.firebasedatabase.app',
        'storageBucket': 'lostandfound-01.firebasestorage.app',
        'projectId': 'lostandfound-01'
    })
    db = firestore.client()
    print("Firebase initialized successfully!")
except Exception as e:
    print(f"Firebase initialization error: {str(e)}")
    print("Please ensure you have valid Firebase credentials set up.")
    db = None  # Set to None so we can check if Firebase is initialized

# Helper function to check Firebase connection
def check_firebase_connection():
    if not db:
        return False
        
    try:
        # Try a simple operation to verify connection
        db.collection('test').limit(1).get()
        return True
    except Exception as e: 
        print(f"Firebase connection test failed: {e}")
        return False

@app.route('/')
def home():
    return "Lost & Found API Server"

@app.route('/api/test', methods=['GET'])
def test_connection():
    print("Test endpoint hit")
    
    # Check Firebase connection
    firebase_status = "Connected" if check_firebase_connection() else "Disconnected"
    
    return jsonify({
        'status': 'success',
        'message': 'Connection to Flask server established successfully!',
        'firebase': firebase_status,
        'version': APP_VERSION,
        'timestamp': datetime.now().isoformat(),
        'server_url': BACKEND_URL
    }), 200

@app.route('/api/items')
def get_items():
    try:
        # Get all items with status 'open'
        items_ref = db.collection('items').where('status', '==', 'open').stream()
        items = []
        for item in items_ref:
            item_data = item.to_dict()
            items.append(item_data)
        return jsonify(items), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/claim-item', methods=['POST'])
def api_claim_item():
    try:
        data = request.json
        claim_id = str(uuid.uuid4())
        
        # Verify item exists
        item_ref = db.collection('items').document(data['itemId'])
        item = item_ref.get()
        
        if not item.exists:
            return jsonify({'error': 'Item not found'}), 404
        
        claim_data = {
            'claim_id': claim_id,
            'item_id': data['itemId'],
            'claim_description': data['claimDescription'],
            'claimant_name': data['claimantName'],
            'claimant_email': data['claimantEmail'],
            'claimant_phone': data['claimantPhone'],
            'status': 'pending',
            'created_at': datetime.now()
        }
        
        db.collection('claims').document(claim_id).set(claim_data)
        
        # Send email notifications
        item_data = item.to_dict()
        send_claim_notification(item_data, claim_data)
        
        return jsonify({
            'message': 'Claim submitted successfully',
            'claim_id': claim_id
        }), 200
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/api/approve-claim', methods=['POST', 'OPTIONS'])
def approve_claim():
    # Handle preflight OPTIONS request
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'success'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response, 204
        
    try:
        print("\n--- STARTING CLAIM APPROVAL PROCESS ---")
        print("Approve claim endpoint hit")
        data = request.json
        print(f"Request data: {data}")
        
        if not db:
            print("Firebase database not initialized properly")
            return jsonify({'error': 'Database connection error'}), 500
            
        claim_id = data.get('claimId')
        
        if not claim_id:
            print("No claim ID provided")
            return jsonify({'error': 'Claim ID is required'}), 400
            
        # Print debug info
        print(f"Processing approval for claim ID: {claim_id}")
        
        try:
            # Get the claim data
            print(f"Accessing claimRequests collection for document {claim_id}")
            claim_ref = db.collection('claimRequests').document(claim_id)
            claim_snap = claim_ref.get()
            
            if not claim_snap.exists:
                print(f"Claim not found: {claim_id}")
                return jsonify({'error': 'Claim not found'}), 404
                
            claim_data = claim_snap.to_dict()
            
            # Update claim status first to ensure database change is made quickly
            print(f"Updating claim status to Approved")
            claim_ref.update({
                'status': 'Approved',
                'actionDate': firestore.SERVER_TIMESTAMP
            })
            
            # Get the item data
            item_type = claim_data.get('itemType', 'lost')
            item_id = claim_data.get('itemId')
            item_collection = 'lostItems' if item_type == 'lost' else 'foundItems'
            
            if item_id:
                # Update item status
                item_ref = db.collection(item_collection).document(item_id)
                item_ref.update({
                    'status': 'Approved'
                })
                
                # Get item data for email
                item_snap = item_ref.get()
                item_data = item_snap.to_dict() if item_snap.exists else {}
            else:
                item_data = {}
            
            # Return success immediately to avoid timeout
            # Start a background thread for email sending that won't block the response
            email_thread = threading.Thread(
                target=send_direct_emails, 
                args=(item_data, claim_data)
            )
            email_thread.daemon = True
            email_thread.start()
            
            print("Database updated successfully, starting email process in background")
            print("--- CLAIM APPROVAL DATABASE UPDATE COMPLETED ---\n")
            
            # Return success to the client without waiting for emails
            return jsonify({
                'message': 'Claim approved successfully. Email notifications will be sent in the background.',
                'status': 'Approved'
            }), 200
            
        except Exception as db_error:
            print(f"Database error: {str(db_error)}")
            return jsonify({'error': f'Database operation failed: {str(db_error)}'}), 500
        
    except Exception as e:
        print(f"Critical error in approve_claim: {str(e)}")
        return jsonify({'error': str(e)}), 500

@app.route('/api/reject-claim', methods=['POST', 'OPTIONS'])
def reject_claim():
    # Handle preflight OPTIONS request
    if request.method == 'OPTIONS':
        response = jsonify({'status': 'success'})
        response.headers.add('Access-Control-Allow-Origin', '*')
        response.headers.add('Access-Control-Allow-Headers', 'Content-Type')
        response.headers.add('Access-Control-Allow-Methods', 'POST, OPTIONS')
        return response, 204
        
    try:
        print("Reject claim endpoint hit")
        data = request.json
        print(f"Request data: {data}")
        
        claim_id = data.get('claimId')
        
        if not claim_id:
            print("No claim ID provided")
            return jsonify({'error': 'Claim ID is required'}), 400
            
        # Print debug info
        print(f"Processing rejection for claim ID: {claim_id}")
        
        try:
            # Get the claim data
            claim_ref = db.collection('claimRequests').document(claim_id)
            claim_snap = claim_ref.get()
            
            if not claim_snap.exists:
                print(f"Claim not found: {claim_id}")
                return jsonify({'error': 'Claim not found'}), 404
                
            # Update the claim status to Rejected
            claim_ref.update({
                'status': 'Rejected',
                'actionDate': firestore.SERVER_TIMESTAMP
            })
            
            print("Claim status updated to Rejected")
            
            # Return success response
            return jsonify({
                'message': 'Claim rejected successfully',
                'status': 'Rejected'
            }), 200
            
        except Exception as db_error:
            print(f"Database error: {str(db_error)}")
            return jsonify({'error': f'Database operation failed: {str(db_error)}'}), 500
            
    except Exception as e:
        print(f"Error in reject_claim: {str(e)}")
        return jsonify({'error': str(e)}), 500

def send_direct_emails(item_data, claim_data):
    """Send emails directly without extra functions to simplify debugging"""
    try:
        print("\n--- BEGIN EMAIL SENDING PROCESS ---")
        
        # Set timeout limits to prevent hanging
        max_email_time = 25  # Maximum seconds to spend on emails
        start_time = time.time()
        
        # Get essential data
        item_name = item_data.get('itemName', 'Unknown Item')
        subject = f"Claim Approved for Item: {item_name}"
        
        # Determine email content based on item type
        is_lost_item = claim_data.get('itemType') == 'lost'
        item_type_text = "lost" if is_lost_item else "found"
        
        # Simplify date handling to minimize processing time
        item_date_str = 'Date not provided'
        for field in ['dateLost', 'dateFound', 'date', 'dateReported', 'dateSubmitted', 'timestamp']:
            if field in item_data and item_data[field]:
                if hasattr(item_data[field], 'strftime'):
                    item_date_str = item_data[field].strftime('%Y-%m-%d')
                else:
                    item_date_str = str(item_data[field])
                break
            elif field in claim_data and claim_data[field]:
                if hasattr(claim_data[field], 'strftime'):
                    item_date_str = claim_data[field].strftime('%Y-%m-%d')
                else:
                    item_date_str = str(claim_data[field])
                break
        
        # Get contact details
        poster_email = item_data.get('email') or item_data.get('userEmail') or item_data.get('ownerEmail')
        poster_name = item_data.get('name') or item_data.get('userName') or item_data.get('ownerName', 'Item Owner/Finder')
        poster_phone = item_data.get('phone') or item_data.get('userPhone') or item_data.get('ownerPhone', 'Not provided')
        
        claimant_email = claim_data.get('email') or claim_data.get('userEmail') or claim_data.get('claimantEmail')
        claimant_name = claim_data.get('name') or claim_data.get('userName') or claim_data.get('claimantName', 'Claimant')
        claimant_phone = claim_data.get('phone') or claim_data.get('userPhone') or claim_data.get('claimantPhone', 'Not provided')
        
        # Check for timeout before sending emails
        if time.time() - start_time > max_email_time:
            print(f"Email preparation took too long, aborting email sending")
            return False
        
        # Track if any emails were sent
        emails_sent = False
        
        # Email to poster/finder
        if poster_email:
            poster_msg = f"""
Hello {poster_name},

Good news! An admin has approved a claim for your {item_type_text} item "{item_name}".

Item Details:
Name: {item_name}
Date {item_type_text}: {item_date_str}

Claimant Details:
Name: {claimant_name}
Email: {claimant_email}
Phone: {claimant_phone}

You can now contact the owner/finder directly through our inbuilt chat system in our lost and found website to arrange for the return of the item.
The chat feature can be accessed through the navigation sidebar on the website.

Thank you for using our Lost & Found service!
"""
            print(f"Attempting to send email to poster: {poster_email}")
            try:
                send_email(poster_email, subject, poster_msg)
                emails_sent = True
                print("Successfully sent email to poster")
            except Exception as e:
                print(f"Failed to send email to poster: {str(e)}")
            
            # Check for timeout before continuing
            if time.time() - start_time > max_email_time:
                print(f"Email process taking too long, aborting further emails")
                return emails_sent
        
        # Email to claimant
        if claimant_email and claimant_email != poster_email:  # Only send if different from poster
            claimant_msg = f"""
Hello {claimant_name},

Good news! Your claim for the {item_type_text} item "{item_name}" has been approved by our admin.

Item Details:
Name: {item_name}
Date {item_type_text}: {item_date_str}

Item Owner/Finder Details:
Name: {poster_name}
Email: {poster_email}
Phone: {poster_phone}

You can now contact the owner/finder directly through our inbuilt chat system in our lost and found website to arrange for the return of the item.
The chat feature can be accessed through the navigation sidebar on the website.

Thank you for using our Lost & Found service!
"""
            print(f"Attempting to send email to claimant: {claimant_email}")
            try:
                send_email(claimant_email, subject, claimant_msg)
                emails_sent = True
                print("Successfully sent email to claimant")
            except Exception as e:
                print(f"Failed to send email to claimant: {str(e)}")
        
        print("--- EMAIL SENDING PROCESS COMPLETED ---\n")
        return emails_sent
    except Exception as e:
        print(f"Error in send_direct_emails: {str(e)}")
        print("--- EMAIL SENDING PROCESS FAILED WITH EXCEPTION ---\n")
        return False

def send_email(to_email, subject, body):
    """Send an email with improved error handling and timeout control"""
    try:
        if not to_email or '@' not in to_email:
            print(f"Invalid email address: {to_email}")
            return False
            
        # Use hardcoded credentials
        from_email = EMAIL_USER
        password = EMAIL_PASSWORD

        # Create message
        msg = MIMEMultipart()
        msg['From'] = from_email
        msg['To'] = to_email
        msg['Subject'] = subject
        msg.attach(MIMEText(body, 'plain'))
        
        # Use a shorter timeout to avoid hanging
        server = smtplib.SMTP('smtp.gmail.com', 587, timeout=10)
        server.starttls()
        server.login(from_email, password)
        
        # Send the email
        text = msg.as_string()
        server.sendmail(from_email, to_email, text)
        server.quit()
        
        return True
    except Exception as e:
        print(f"Email error: {str(e)}")
        # Always try to close the connection
        try:
            if 'server' in locals() and server:
                server.quit()
        except:
            pass
        return False

if __name__ == '__main__':
    print("\n=== STARTING LOST & FOUND SERVICE ===")
    print("Initializing resources...")
    
    # Verify Firebase connection
    firebase_status = "CONNECTED" if check_firebase_connection() else "DISCONNECTED"
    print(f"Firebase status: {firebase_status}")
    
    # Verify email configuration
    print(f"Email configuration: {EMAIL_USER}")
    
    # Set up signal handler for graceful shutdown
    def signal_handler(sig, frame):
        print("\nShutdown signal received. Cleaning up resources...")
        print("Exiting application. Goodbye!")
        sys.exit(0)
        
    # Register signal handlers
    signal.signal(signal.SIGINT, signal_handler)  # Ctrl+C
    if hasattr(signal, 'SIGBREAK'):  # Windows Ctrl+Break
        signal.signal(signal.SIGBREAK, signal_handler)
    
    # Get the port from environment variable (provided by Render)
    # Default to 10000 if not specified
    port = int(os.environ.get('PORT', 10000))
    
    # Run the app listening on all interfaces (0.0.0.0)
    # This is required for Render deployment
    print(f"\nStarting Flask server on port {port}")
    app.run(host='0.0.0.0', port=port, debug=False) 
