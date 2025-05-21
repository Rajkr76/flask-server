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
            # Get the claim data directly first (outside transaction)
            print(f"Accessing claimRequests collection for document {claim_id}")
            claim_ref = db.collection('claimRequests').document(claim_id)
            claim_snap = claim_ref.get()
            
            if not claim_snap.exists:
                print(f"Claim not found: {claim_id}")
                return jsonify({'error': 'Claim not found'}), 404
                
            claim_data = claim_snap.to_dict()
            if not claim_data:
                print(f"Claim data is empty for claim ID: {claim_id}")
                return jsonify({'error': 'Claim data is empty'}), 500
                
            print(f"Claim data retrieved with keys: {list(claim_data.keys())}")
            
            # Validate claim data - use the actual field names from your Firebase
            # Check if using 'itemType' and 'itemId', if not found try alternative field names
            item_type_field = None
            item_id_field = None
            
            if 'itemType' in claim_data:
                item_type_field = 'itemType'
            # Add alternative field names if needed
            
            if 'itemId' in claim_data:
                item_id_field = 'itemId'
            # Add alternative field names if needed
            
            if not item_type_field or not item_id_field:
                print(f"Missing required fields in claim data. Available keys: {list(claim_data.keys())}")
                return jsonify({'error': 'Missing required claim data'}), 400
                
            # Get the item data - using correct collection name based on itemType
            item_type = claim_data.get(item_type_field)
            item_id = claim_data.get(item_id_field)
            
            print(f"Item type: {item_type}, Item ID: {item_id}")
            
            # Use the correct collection based on the item type
            item_collection = 'lostItems' if item_type == 'lost' else 'foundItems'
            print(f"Using collection: {item_collection}, Item ID: {item_id}")
            
            item_ref = db.collection(item_collection).document(item_id)
            item_snap = item_ref.get()
            
            if not item_snap.exists:
                print(f"Item not found in {item_collection} collection with ID: {item_id}")
                return jsonify({'error': 'Item not found'}), 404
                
            item_data = item_snap.to_dict()
            if not item_data:
                print(f"Item data is empty for item ID: {item_id}")
                return jsonify({'error': 'Item data is empty'}), 500
                
            print(f"Item data retrieved successfully with keys: {list(item_data.keys())}")
            
            # These will be used for email sending
            item_name = item_data.get('itemName', 'Unknown Item')
            print(f"Item name: {item_name}")
            
            # Try to update claim status - with error handling and using the correct case for status
            try:
                print(f"Updating claim status to Approved")
                claim_ref.update({
                    'status': 'Approved',  # Match the capitalization used in your database
                    'actionDate': firestore.SERVER_TIMESTAMP
                })
                print(f"Claim status updated successfully")
            except Exception as claim_update_error:
                print(f"Error updating claim status: {str(claim_update_error)}")
                # Continue to try updating the item
            
            # Try to update item status - with error handling
            try:
                print(f"Updating item status to Approved")
                item_ref.update({
                    'status': 'Approved'  # Match the capitalization used in your database
                })
                print(f"Item status updated successfully")
            except Exception as item_update_error:
                print(f"Error updating item status: {str(item_update_error)}")
                # Continue to email sending even if update fails
                
            print("Database operations completed")
            
        except Exception as db_error:
            print(f"Database error: {str(db_error)}")
            return jsonify({'error': f'Database operation failed: {str(db_error)}'}), 500
        
        # After database operations, try to send emails
        # Use threading to prevent blocking the server during email sending
        print("Starting email sending process in background thread")
        
        def send_emails_async(item_data, claim_data):
            try:
                print("Starting email process in separate thread")
                send_result = send_direct_emails(item_data, claim_data)
                print(f"Email thread completed with result: {send_result}")
            except Exception as thread_err:
                print(f"Error in email thread: {str(thread_err)}")
        
        # Start a new thread for email sending
        email_thread = threading.Thread(target=send_emails_async, args=(item_data, claim_data))
        email_thread.daemon = True  # Make sure thread dies when main thread exits
        email_thread.start()
        
        # Return success immediately without waiting for email
        print("--- CLAIM APPROVAL COMPLETED SUCCESSFULLY ---\n")
        return jsonify({'message': 'Claim approved successfully. Emails will be sent in the background.'}), 200
        
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
        print(f"Item data keys: {list(item_data.keys())}")
        print(f"Claim data keys: {list(claim_data.keys())}")
        
        # Get essential data with defaults for missing fields
        item_name = item_data.get('itemName', 'Unknown Item')
        subject = f"Claim Approved for Item: {item_name}"
        
        # Determine email content based on item type
        is_lost_item = claim_data.get('itemType') == 'lost'
        item_type_text = "lost" if is_lost_item else "found"
        
        # Item date handling - check all possible date fields
        item_date = None
        date_field = 'dateLost' if is_lost_item else 'dateFound'
        backup_date_fields = ['date', 'dateReported', 'dateSubmitted', 'timestamp']
        
        # Try the primary date field first
        if date_field in item_data and item_data[date_field]:
            item_date = item_data[date_field]
            print(f"Using primary date field: {date_field}")
        elif date_field in claim_data and claim_data[date_field]:
            item_date = claim_data[date_field]
            print(f"Using primary date field from claim data: {date_field}")
        else:
            # Try backup date fields in both item_data and claim_data
            for field in backup_date_fields:
                # Check item_data first
                if field in item_data and item_data[field]:
                    item_date = item_data[field]
                    print(f"Using backup date field from item data: {field}")
                    break
                # Then check claim_data
                elif field in claim_data and claim_data[field]:
                    item_date = claim_data[field]
                    print(f"Using backup date field from claim data: {field}")
                    break
        
        # Format the date properly
        if not item_date:
            item_date_str = 'Date not provided'
            print(f"No date found in item data")
        else:
            print(f"Raw date value: {item_date}, Type: {type(item_date)}")
            # Handle different date formats
            try:
                if hasattr(item_date, 'timestamp'):  # Firestore timestamp
                    try:
                        item_date_str = item_date.strftime('%Y-%m-%d')
                    except Exception as e:
                        print(f"Error formatting Firestore timestamp: {e}")
                        item_date_str = str(item_date)
                elif isinstance(item_date, dict) and 'seconds' in item_date:  # Timestamp as dict
                    try:
                        from datetime import datetime
                        item_date_str = datetime.fromtimestamp(item_date['seconds']).strftime('%Y-%m-%d')
                    except Exception as e:
                        print(f"Error formatting timestamp dict: {e}")
                        item_date_str = str(item_date)
                elif isinstance(item_date, (int, float)):  # Unix timestamp
                    try:
                        from datetime import datetime
                        item_date_str = datetime.fromtimestamp(item_date).strftime('%Y-%m-%d')
                    except Exception as e:
                        print(f"Error formatting timestamp number: {e}")
                        item_date_str = str(item_date)
                elif isinstance(item_date, str):  # String date
                    item_date_str = item_date
                else:
                    item_date_str = str(item_date)
            except Exception as date_err:
                print(f"Error formatting date: {date_err}")
                item_date_str = "Unknown date format"
        
        # Print all available fields to help debugging
        print(f"Item data fields: {list(item_data.keys())}")
        print(f"Claim data fields: {list(claim_data.keys())}")
        
        # Get poster/finder details - check all possible field locations
        poster_email = None
        for field in ['email', 'userEmail', 'ownerEmail']:
            if field in item_data and item_data[field]:
                poster_email = item_data[field]
                print(f"Found poster email in item_data[{field}]: {poster_email}")
                break
            if field in claim_data and claim_data[field]:
                poster_email = claim_data[field]
                print(f"Found poster email in claim_data[{field}]: {poster_email}")
                break
                
        if not poster_email:
            print("WARNING: No poster email found in any field")
            
        # Get poster name from various possible fields
        poster_name = None
        for field in ['name', 'userName', 'ownerName']:
            if field in item_data and item_data[field]:
                poster_name = item_data[field]
                print(f"Found poster name in item_data[{field}]: {poster_name}")
                break
            if field in claim_data and claim_data[field]:
                poster_name = claim_data[field]
                print(f"Found poster name in claim_data[{field}]: {poster_name}")
                break
                
        if not poster_name:
            poster_name = 'Item Owner/Finder'
            print("No poster name found, using default")
            
        # Get poster phone from various possible fields
        poster_phone = None
        for field in ['phone', 'userPhone', 'ownerPhone']:
            if field in item_data and item_data[field]:
                poster_phone = item_data[field]
                print(f"Found poster phone in item_data[{field}]: {poster_phone}")
                break
            if field in claim_data and claim_data[field]:
                poster_phone = claim_data[field]
                print(f"Found poster phone in claim_data[{field}]: {poster_phone}")
                break
                
        if not poster_phone:
            poster_phone = 'Not provided'
            print("No poster phone found, using default")
            
        # Use the same approach for claimant details
        claimant_email = None
        for field in ['email', 'userEmail', 'claimantEmail']:
            if field in claim_data and claim_data[field]:
                claimant_email = claim_data[field]
                print(f"Found claimant email in claim_data[{field}]: {claimant_email}")
                break
                
        if not claimant_email:
            print("WARNING: No claimant email found in claim data")
            
        claimant_name = None
        for field in ['name', 'userName', 'claimantName']:
            if field in claim_data and claim_data[field]:
                claimant_name = claim_data[field]
                print(f"Found claimant name in claim_data[{field}]: {claimant_name}")
                break
                
        if not claimant_name:
            claimant_name = 'Claimant'
            print("No claimant name found, using default")
            
        claimant_phone = None
        for field in ['phone', 'userPhone', 'claimantPhone']:
            if field in claim_data and claim_data[field]:
                claimant_phone = claim_data[field]
                print(f"Found claimant phone in claim_data[{field}]: {claimant_phone}")
                break
                
        if not claimant_phone:
            claimant_phone = 'Not provided'
            print("No claimant phone found, using default")
        
        print(f"Final contact details:")
        print(f"Poster: {poster_name}, {poster_email}, {poster_phone}")
        print(f"Claimant: {claimant_name}, {claimant_email}, {claimant_phone}")
        
        # If no valid emails found, we can't send any emails
        if not poster_email and not claimant_email:
            print("ERROR: No valid email addresses found for either poster or claimant")
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
            if send_email(poster_email, subject, poster_msg):
                emails_sent = True
                print("Successfully sent email to poster")
            else:
                print("Failed to send email to poster")
        else:
            print("Skipping poster email - no valid email address")
        
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
            if send_email(claimant_email, subject, claimant_msg):
                emails_sent = True
                print("Successfully sent email to claimant")
            else:
                print("Failed to send email to claimant")
        else:
            print("Skipping claimant email - no valid email address or same as poster")
        
        # If no emails were sent despite having recipients, consider it a partial failure
        if not emails_sent and (poster_email or claimant_email):
            print("No emails were sent successfully despite having recipients")
            print("--- EMAIL SENDING PROCESS FAILED ---\n")
            return False
            
        print("--- EMAIL SENDING PROCESS COMPLETED ---\n")
        return True
    except Exception as e:
        print(f"Error in send_direct_emails: {str(e)}")
        print("--- EMAIL SENDING PROCESS FAILED WITH EXCEPTION ---\n")
        return False

def send_email(to_email, subject, body):
    """Send an email with improved error handling"""
    try:
        if not to_email or '@' not in to_email:
            print(f"Invalid email address: {to_email}")
            return False
            
        # Use hardcoded credentials
        from_email = EMAIL_USER
        password = EMAIL_PASSWORD

        print(f"Preparing to send email to {to_email}")
        
        # Create message
        try:
            msg = MIMEMultipart()
            msg['From'] = from_email
            msg['To'] = to_email
            msg['Subject'] = subject
            msg.attach(MIMEText(body, 'plain'))
            print(f"Email message prepared for {to_email}")
        except Exception as msg_error:
            print(f"Error creating email message: {str(msg_error)}")
            return False

        # Attempt to connect to SMTP server with retries
        max_retries = 2  # Increased retries since we're not blocking the main thread
        retry_delay = 1  # Seconds between retries
        
        for attempt in range(max_retries):
            try:
                print(f"SMTP connection attempt {attempt+1}/{max_retries}")
                # Using a shorter timeout to prevent long blocking
                server = smtplib.SMTP('smtp.gmail.com', 587, timeout=5)
                print("SMTP connection established")
                
                # Enable debug output
                server.set_debuglevel(1)
                
                # Start TLS connection
                print("Starting TLS")
                server.starttls()
                
                # Login with credentials
                print(f"Logging in as {from_email}")
                server.login(from_email, password)
                
                # Send the email
                print(f"Sending email from {from_email} to {to_email}")
                text = msg.as_string()
                server.sendmail(from_email, to_email, text)
                
                # Close the connection properly
                print("Closing SMTP connection")
                server.quit()
                
                print(f"Email sent successfully to {to_email}")
                return True
            except smtplib.SMTPAuthenticationError as auth_err:
                print(f"SMTP Authentication error: {str(auth_err)}")
                # Print more detailed error for diagnosis
                print(f"Check if app password '{password[:4]}****' is correct and enabled for SMTP")
                # No need to retry for authentication errors
                return False
            except smtplib.SMTPServerDisconnected as disc_err:
                print(f"SMTP Server Disconnected: {str(disc_err)}")
                if attempt < max_retries - 1:
                    print(f"Waiting {retry_delay} seconds before retry")
                    time.sleep(retry_delay)
            except smtplib.SMTPException as smtp_err:
                print(f"SMTP Error: {str(smtp_err)}")
                if attempt < max_retries - 1:
                    print(f"Waiting {retry_delay} seconds before retry")
                    time.sleep(retry_delay)
            except Exception as e:
                print(f"Unexpected error sending email to {to_email}: {str(e)}")
                if attempt < max_retries - 1:
                    print(f"Waiting {retry_delay} seconds before retry")
                    time.sleep(retry_delay)
            finally:
                # Make sure to close the connection if it's still open
                try:
                    if 'server' in locals() and server:
                        server.quit()
                except:
                    pass
        
        print(f"Failed to send email to {to_email} after {max_retries} attempts")
        return False
    except Exception as outer_err:
        print(f"Fatal error in send_email function: {str(outer_err)}")
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