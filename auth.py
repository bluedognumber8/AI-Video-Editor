"""
Authentication module with instant login fix (No extra packages)
"""

import streamlit as st
import os
from dotenv import load_dotenv

load_dotenv()

# Read Password from .env
PASSWORD = os.environ.get("APP_PASSWORD", "nazarov")

def check_password():
    """
    Password check with Enter key support.
    Returns True if authenticated.
    """
    # Check current active session state
    if st.session_state.get("authenticated", False):
        return True
    
    if "password_value" not in st.session_state:
        st.session_state["password_value"] = ""
    
    def submit_password():
        if st.session_state["password_value"] == PASSWORD:
            st.session_state["authenticated"] = True
            st.session_state["auth_error"] = False
            # INSTANT REFRESH FIX (No more double clicking)
            st.rerun()
        else:
            st.session_state["auth_error"] = True
    
    # Custom CSS
    st.markdown("""
    <style>
        .stApp { background: linear-gradient(135deg, #667eea 0%, #764ba2 100%); }
        .main > div { display: flex; justify-content: center; align-items: center; min-height: 100vh; }
        .login-card { background: white; padding: 2.5rem; border-radius: 20px; box-shadow: 0 20px 60px rgba(0,0,0,0.3); max-width: 400px; width: 90%; margin: 0 auto; }
        #MainMenu {visibility: hidden;} footer {visibility: hidden;}
        div[data-testid="stForm"] { border: none; padding: 0; background: transparent; }
        .stTextInput > div > div > input { border-radius: 10px !important; border: 2px solid #e0e0e0 !important; padding: 0.8rem !important; font-size: 1rem !important; }
        .stTextInput > div > div > input:focus { border-color: #667eea !important; box-shadow: 0 0 0 3px rgba(102, 126, 234, 0.1) !important; }
        .stButton > button { width: 100%; border-radius: 10px !important; padding: 0.8rem !important; font-size: 1.1rem !important; font-weight: 600 !important; background: linear-gradient(135deg, #667eea 0%, #764ba2 100%) !important; color: white !important; border: none !important; margin-top: 1rem !important; }
        .error-box { background: #fee; color: #c33; padding: 0.8rem; border-radius: 10px; text-align: center; margin-top: 1rem; border: 1px solid #fcc; }
    </style>
    """, unsafe_allow_html=True)
    
    col1, col2, col3 = st.columns([1, 2, 1])
    with col2:
        st.markdown("""
        <div class="login-card">
            <div style="text-align: center; font-size: 3rem; margin-bottom: 1rem;">🎬</div>
            <div style="text-align: center; font-size: 1.8rem; font-weight: 700; color: #333; margin-bottom: 0.5rem;">AI Video Editor</div>
            <div style="text-align: center; color: #666; margin-bottom: 2rem;">Enter password to continue</div>
        </div>
        """, unsafe_allow_html=True)
        
        with st.form(key="login_form"):
            st.text_input("Password", type="password", key="password_value", label_visibility="collapsed", placeholder="Enter password and press Enter...")
            submitted = st.form_submit_button("🔐 Login", use_container_width=True)
            if submitted:
                submit_password()
        
        if st.session_state.get("auth_error", False):
            st.markdown('<div class="error-box">❌ Incorrect password. Please try again.</div>', unsafe_allow_html=True)
    
    return False