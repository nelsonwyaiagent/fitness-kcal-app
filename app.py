import streamlit as st
import supabase
import requests
import json
import re
import os
from datetime import datetime, timedelta
from plotly import graph_objects as go

# Use OpenAI client for MiniMax
try:
    from openai import OpenAI
except:
    os.system("pip install openai")
    from openai import OpenAI



# Page config
st.set_page_config(page_title="Fitness Kcal App", page_icon="💪", layout="wide")

# Supabase client
@st.cache_resource
def get_supabase_client():
    return supabase.create_client(
        st.secrets["NEXT_PUBLIC_SUPABASE_URL"],
        st.secrets["NEXT_PUBLIC_SUPABASE_ANON_KEY"]
    )

supabase = get_supabase_client()

# MET values
MET_VALUES = {
    "tennis": 7.3, "squash": 7.9, "pilates": 3.0, "running": 9.8,
    "walking": 3.5, "swimming": 6.0, "cycling": 6.8, "yoga": 2.5,
    "gym": 5.0, "weights": 4.0, "badminton": 5.2, "basketball": 6.0
}

def calculate_calories(activity: str, duration_mins: int, weight_kg: float = 70) -> int:
    met = MET_VALUES.get(activity.lower(), 4.0)
    return int(met * weight_kg * (duration_mins / 60))

# Session state
if "user_id" not in st.session_state:
    st.session_state.user_id = None
if "logged_in" not in st.session_state:
    st.session_state.logged_in = False

# Sidebar
st.sidebar.title("💪 Fitness Kcal")
page = st.sidebar.radio("Navigate", ["Today", "Plan", "Chat", "Profile"])

def sign_in(email: str, password: str):
    try:
        response = supabase.auth.sign_in_with_password({"email": email, "password": password})
        if response.user:
            st.session_state.user_id = response.user.id
            st.session_state.logged_in = True
            return response.user
        else:
            st.error("Invalid email or password")
            return None
    except Exception as e:
        st.error(f"Error: {e}")
        return None

def sign_up(email: str, password: str):
    try:
        response = supabase.auth.sign_up({"email": email, "password": password})
        # After signup, try to sign in directly
        if response.user:
            st.session_state.user_id = response.user.id
            st.session_state.logged_in = True
            return response.user
        return None
    except Exception as e:
        st.error(f"Error: {e}")
        return None


def get_user_data():
    if not st.session_state.user_id:
        return None, None
    profile_resp = supabase.table("profiles").select("*").eq("id", st.session_state.user_id).execute()
    plan_resp = supabase.table("weight_plans").select("*").eq("user_id", st.session_state.user_id).eq("is_active", True).execute()
    return profile_resp.data[0] if profile_resp.data else None, plan_resp.data[0] if plan_resp.data else None

def get_today_data():
    if not st.session_state.user_id:
        return [], [], {"steps": 0, "active_energy_kcal": 0}
    today = datetime.now().date().isoformat()
    tomorrow = (datetime.now() + timedelta(days=1)).date().isoformat()
    meals_resp = supabase.table("meals").select("*").eq("user_id", st.session_state.user_id).gte("timestamp", today).lt("timestamp", tomorrow).execute()
    workouts_resp = supabase.table("workouts").select("*").eq("user_id", st.session_state.user_id).gte("timestamp", today).lt("timestamp", tomorrow).execute()
    health_resp = supabase.table("health_metrics").select("*").eq("user_id", st.session_state.user_id).eq("date", today).execute()
    return meals_resp.data or [], workouts_resp.data or [], health_resp.data[0] if health_resp.data else {"steps": 0, "active_energy_kcal": 0}

def get_weekly_data():
    if not st.session_state.user_id:
        return []
    days = []
    for i in range(6, -1, -1):
        date = (datetime.now() - timedelta(days=i)).date().isoformat()
        next_date = (datetime.now() - timedelta(days=i-1)).date().isoformat() if i > 0 else (datetime.now() + timedelta(days=1)).date().isoformat()
        meals_resp = supabase.table("meals").select("kcal").eq("user_id", st.session_state.user_id).gte("timestamp", date).lt("timestamp", next_date).execute()
        workouts_resp = supabase.table("workouts").select("kcal_burned").eq("user_id", st.session_state.user_id).gte("timestamp", date).lt("timestamp", next_date).execute()
        health_resp = supabase.table("health_metrics").select("active_energy_kcal").eq("user_id", st.session_state.user_id).eq("date", date).execute()
        eaten = sum(m.get("kcal", 0) for m in meals_resp.data)
        burned = sum(w.get("kcal_burned", 0) for w in workouts_resp.data) + sum(h.get("active_energy_kcal", 0) for h in health_resp.data)
        days.append({"date": date, "day": (datetime.now() - timedelta(days=i)).strftime("%a"), "eaten": eaten, "burned": burned, "net": eaten - burned})
    return days

def analyze_text_meal(description: str) -> dict:
    try:
        from openai import OpenAI
        client = OpenAI(
            api_key=st.secrets["MINIMAX_API_KEY"],
            base_url="https://api.minimax.io/v1"
        )
        prompt = (
            f"Analyze meal: {description}. "
            "Return ONLY a valid JSON object with keys: description, kcal, protein, carbs, fat. "
            "The values for kcal, protein, carbs, and fat MUST be pure numbers (integers) without any units or text. "
            "Do not use markdown formatting."
        )
        response = client.chat.completions.create(
            model="MiniMax-M3",
            messages=[{"role": "user", "content": prompt}],
            max_tokens=800
        )
        content = response.choices[0].message.content

        # ==========================================
        # DEBUGGING: SHOW RAW LLM OUTPUT IN THE APP
        # ==========================================
        # st.info(f"**Raw LLM Output:**\n\n{content}")
        
        import re
        # Try multiple parsing approaches
        try:
            # Try finding JSON with any format
            json_match = re.search(r'\{[\s\S]*\}', content)
            if json_match:
                result_str = json_match.group()
                # Replace single quotes with double quotes for JSON
                result_str = result_str.replace("'", '"')
                result = json.loads(result_str)
                return {
                  "description": result.get("description", description),
                  # Using a helper to strip out any accidental text/units before converting to int
                  "kcal": int(re.sub(r'[^\d]', '', str(result.get("kcal", 0))) or 0),
                  "protein": int(re.sub(r'[^\d]', '', str(result.get("protein", 0))) or 0),
                  "carbs": int(re.sub(r'[^\d]', '', str(result.get("carbs", 0))) or 0),
                  "fat": int(re.sub(r'[^\d]', '', str(result.get("fat", 0))) or 0)
                }
        except Exception as e:
            # Changed to st.error so it highlights in red on your screen
            st.error(f"Parse error: {e}") 
        
        return {"description": description, "kcal": 0, "protein": 0, "carbs": 0, "fat": 0}
        
    except Exception as e:  # <--- FIX: Changed "except:" to "except Exception as e:"
        # ==========================================
        # DEBUGGING: CATCH API/CONNECTION ERRORS
        # ==========================================
        st.error(f"API or Connection Error: {e}")
        return {"description": description, "kcal": 0, "protein": 0, "carbs": 0, "fat": 0}

def analyze_meal_image(image_bytes: bytes) -> dict:
    from openai import OpenAI
    import base64
    image_b64 = base64.b64encode(image_bytes).decode()
    try:
        client = OpenAI(
            api_key=st.secrets["MINIMAX_API_KEY"],
            base_url="https://api.minimax.io/v1"
        )
        response = client.chat.completions.create(
            model="MiniMax-M3",
            messages=[{"role": "user", "content": [
                {"type": "image_url", "image_url": {"url": f"data:image/jpeg;base64,{image_b64}"}},
                {"type": "text", "text": "Analyze this meal. Return ONLY JSON with: description, kcal, protein, carbs, fat. No markdown."}
            ]}],
            max_tokens=800
        )
        content = response.choices[0].message.content
        import re
        json_match = re.search(r'\{[\s\S]*\}', content)
        return json.loads(json_match.group()) if json_match else {}
    except Exception as e:
        st.error(f"AI Error: {e}")
        return {}

# Auth check
if not st.session_state.logged_in:
    st.title("🏋️ Fitness Kcal App")
    st.write("Track your diet, workouts, and achieve weight loss goals!")
    with st.form("auth_form"):
        email = st.text_input("Email")
        password = st.text_input("Password", type="password")
        col1, col2 = st.columns(2)
        sign_in_btn = col1.form_submit_button("Sign In")
        sign_up_btn = col2.form_submit_button("Sign Up")
        if sign_in_btn:
            user = sign_in(email, password)
            if user:
                st.success("Signed in!")
                st.rerun()
        if sign_up_btn:
            user = sign_up(email, password)
            if user:
                st.success("Signed up! Please sign in.")
else:
    if page == "Today":
        st.title("📅 Today")
        meals, workouts, health = get_today_data()
        profile, plan = get_user_data()
        eaten = sum(m.get("kcal", 0) for m in meals)
        burned = sum(w.get("kcal_burned", 0) for w in workouts) + health.get("active_energy_kcal", 0)
        target = plan.get("daily_kcal_target", 2000) if plan else 2000
        remaining = target - (eaten - burned)
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Eaten", f"{eaten} kcal")
        col2.metric("Burned", f"{burned} kcal")
        col3.metric("Target", f"{target} kcal")
        col4.metric("Remaining", f"{remaining} kcal", delta=remaining)
        # Concentric rings visualization
        col1, col2, col3, col4 = st.columns(4)
        col1.metric("Eaten", f"{eaten}")
        col2.metric("Burned", f"{burned}")
        col3.metric("Target", f"{target}")
        col4.metric("Remaining", f"{remaining}", delta=remaining)
        st.metric("Steps", f"{health.get('steps', 0):,}")
        st.subheader("📋 Today's Timeline")
        events = []
        for m in meals:
            events.append((m["timestamp"], f"🍽️ {m['description']}", m["kcal"], m["id"], "meals"))
        for w in workouts:
            events.append((w["timestamp"], f"🏃 {w['activity_type']}", -w["kcal_burned"], w["id"], "workouts"))
        events.sort(key=lambda x: x[0], reverse=True)
        for ts, desc, kcal, rec_id, table in events[:10]:
            col1, col2 = st.columns([5, 1])
            col1.write(f"{desc}: {'+' if kcal > 0 else ''}{kcal} kcal")
            if col2.button("🗑️", key=f"del_{rec_id}"):
                supabase.table(table).delete().eq("id", rec_id).execute()
                st.success("Deleted!")
                st.rerun()
        if not events:
            st.info("No meals or workouts logged today.")
        st.subheader("➕ Quick Add")
        tab1, tab2 = st.tabs(["🍽️ Meal", "🏃 Workout"])
        with tab1:
            with st.form("add_meal"):
                desc = st.text_input("Description (e.g., 2 eggs with toast)")
                meal_type = st.selectbox("Type", ["breakfast", "lunch", "dinner", "snack"])
                
                # Initialize text result
                if "text_meal_result" not in st.session_state:
                    st.session_state.text_meal_result = None
                
                # Analyze button
                if st.form_submit_button("🤖 AI: Estimate Nutrition"):
                    if desc:
                        with st.spinner("Analyzing..."):
                            result = analyze_text_meal(desc)
                            st.session_state.text_meal_result = result
                
                # Show result if available
                if st.session_state.text_meal_result:
                    result = st.session_state.text_meal_result
                    st.success("✅ Analysis complete!")
                    st.json(result)
                    kcal = st.number_input("Calories", min_value=0, value=int(result.get("kcal", 0)))
                    protein = st.number_input("Protein (g)", min_value=0, value=int(result.get("protein", 0)))
                    carbs = st.number_input("Carbs (g)", min_value=0, value=int(result.get("carbs", 0)))
                    fat = st.number_input("Fat (g)", min_value=0, value=int(result.get("fat", 0)))
                else:
                    kcal = st.number_input("Calories", min_value=0, value=0)
                    protein = st.number_input("Protein (g)", min_value=0, value=0)
                    carbs = st.number_input("Carbs (g)", min_value=0, value=0)
                    fat = st.number_input("Fat (g)", min_value=0, value=0)
                
                if st.form_submit_button("Add Meal"):
                    supabase.table("meals").insert({"user_id": st.session_state.user_id, "description": desc, "kcal": kcal, "protein_grams": protein, "carbs_grams": carbs, "fat_grams": fat, "meal_type": meal_type}).execute()
                    st.session_state.text_meal_result = None
                    st.success("Meal added!")
                    st.rerun()
            st.write("---")
            st.write("📸 **AI Photo Analysis**")
            uploaded = st.file_uploader("Upload meal photo", type=["jpg", "jpeg", "png"], key="meal_photo")
            
            # Initialize AI result in session state
            if "ai_meal_result" not in st.session_state:
                st.session_state.ai_meal_result = None
            
            # Show uploaded photo
            if uploaded:
                st.image(uploaded, width=200)
                
                if st.button("Analyze with AI", key="analyze_btn"):
                    with st.spinner("Analyzing..."):
                        result = analyze_meal_image(uploaded.read())
                        if result:
                            st.session_state.ai_meal_result = result
                            st.rerun()
            
            # Auto-fill form with AI result
            if st.session_state.ai_meal_result:
                result = st.session_state.ai_meal_result
                st.success("✅ Analysis complete!")
                st.json(result)
                
                with st.form("add_ai_meal"):
                    desc = st.text_input("Description", value=result.get("description", ""))
                    kcal = st.number_input("Calories", min_value=0, value=result.get("kcal", 0))
                    protein = st.number_input("Protein (g)", min_value=0, value=int(result.get("protein", 0)))
                    carbs = st.number_input("Carbs (g)", min_value=0, value=int(result.get("carbs", 0)))
                    fat = st.number_input("Fat (g)", min_value=0, value=int(result.get("fat", 0)))
                    meal_type = st.selectbox("Type", ["breakfast", "lunch", "dinner", "snack"], index=["breakfast", "lunch", "dinner", "snack"].index(result.get("meal_type", "lunch")) if result.get("meal_type") in ["breakfast", "lunch", "dinner", "snack"] else 1)
                    
                    col1, col2 = st.columns(2)
                    save_btn = col1.form_submit_button("Add Meal")
                    clear_btn = col2.form_submit_button("Clear")
                    
                    if save_btn:
                        supabase.table("meals").insert({"user_id": st.session_state.user_id, "description": desc, "kcal": kcal, "protein_grams": protein, "carbs_grams": carbs, "fat_grams": fat, "meal_type": meal_type, "source": "ai_vision"}).execute()
                        st.success("Meal saved!")
                        st.session_state.ai_meal_result = None
                        st.rerun()
                    
                    if clear_btn:
                        st.session_state.ai_meal_result = None
                        st.rerun()
        with tab2:
            with st.form("add_workout"):
                activity = st.text_input("Activity")
                duration = st.number_input("Duration (mins)", min_value=1, value=30)
                kcal_input = st.number_input("Calories burned", min_value=0, value=0)
                calc_btn = st.form_submit_button("Calculate")
                if calc_btn and activity and duration:
                    st.info(f"Estimated: {calculate_calories(activity, duration)} kcal")
                if st.form_submit_button("Add Workout"):
                    supabase.table("workouts").insert({"user_id": st.session_state.user_id, "activity_type": activity, "duration_mins": duration, "kcal_burned": kcal_input or calculate_calories(activity, duration), "source": "manual"}).execute()
                    st.success("Workout added!")
                    st.rerun()
    elif page == "Plan":
        st.title("📊 Plan")
        weekly = get_weekly_data()
        profile, plan = get_user_data()
        target = plan.get("daily_kcal_target", 2000) if plan else 2000
        fig = go.Figure()
        fig.add_trace(go.Bar(x=[d["day"] for d in weekly], y=[d["eaten"] for d in weekly], name="Eaten", marker_color="#10b981"))
        fig.add_trace(go.Bar(x=[d["day"] for d in weekly], y=[d["burned"] for d in weekly], name="Burned", marker_color="#3b82f6"))
        fig.add_hline(y=target, line_dash="dash", line_color="emerald", annotation_text=f"Target: {target}")
        fig.update_layout(title="Weekly Calories", barmode="group")
        st.plotly_chart(fig, use_container_width=True)
        st.subheader("This Week")
        for d in weekly:
            st.write(f"**{d['day']}**: {d['eaten']} eaten, {d['burned']} burned, {d['net']} net")
    elif page == "Chat":
        st.title("💬 Chat")
        if "chat_history" not in st.session_state:
            st.session_state.chat_history = []
        for role, msg in st.session_state.chat_history:
            with st.chat_message(role):
                st.write(msg)
        query = st.chat_input("Ask about your fitness...")
        if query:
            st.session_state.chat_history.append(("user", query))
            meals, workouts, health = get_today_data()
            eaten = sum(m.get("kcal", 0) for m in meals)
            burned = sum(w.get("kcal_burned", 0) for w in workouts) + health.get("active_energy_kcal", 0)
            target = 2000
            remaining = target - (eaten - burned)
            query_lower = query.lower()
            # Use MiniMax for AI responses
            try:
                from openai import OpenAI
                client = OpenAI(
                    api_key=st.secrets["MINIMAX_API_KEY"],
                    base_url="https://api.minimax.io/v1"
                )
                ai_response = client.chat.completions.create(
                    model="MiniMax-M3",
                    messages=[
                        {"role": "system", "content": "You are a helpful fitness coach. Give practical advice about meals, workouts, and nutrition."},
                        {"role": "user", "content": f"User data: {eaten} kcal eaten, {burned} burned, target {target} kcal. Question: {query}"}
                    ],
                    max_tokens=500
                )
                response = ai_response.choices[0].message.content
            except Exception as e:
                # Fallback to simple responses
                if "how am i" in query_lower or "progress" in query_lower:
                    response = f"You're {abs(remaining)} kcal under/over your target. {eaten} eaten, {burned} burned."
                elif "log" in query_lower or "add" in query_lower:
                    response = "Use the Today page to log meals and workouts!"
                else:
                    response = "I'm here to help! Ask about your progress."
            with st.chat_message("assistant"):
                st.write(response)
            st.session_state.chat_history.append(("assistant", response))
        if st.button("Clear Chat"):
            st.session_state.chat_history = []
            st.rerun()
    elif page == "Profile":
        st.title("👤 Profile")
        profile, plan = get_user_data()
        if plan:
            col1, col2 = st.columns(2)
            col1.metric("Start Weight", f"{plan.get('start_weight_kg', 0)} kg")
            col2.metric("Target Weight", f"{plan.get('target_weight_kg', 0)} kg")
            col1.metric("Daily Target", f"{plan.get('daily_kcal_target', 0)} kcal")
            col2.metric("Weekly Loss", f"{plan.get('weekly_loss_rate_kg', 0)} kg")
        if profile:
            st.write(f"**Height**: {profile.get('height_cm', 'N/A')} cm")
            st.write(f"**Gender**: {profile.get('gender', 'N/A')}")
            st.write(f"**Baseline TDEE**: {profile.get('baseline_tdee', 0)} kcal")
        st.subheader("⚖️ Log Weight")
        with st.form("log_weight"):
            weight = st.number_input("Weight (kg)", min_value=0.0, value=0.0)
            if st.form_submit_button("Log Weight"):
                supabase.table("weight_logs").insert({"user_id": st.session_state.user_id, "weight_kg": weight}).execute()
                st.success("Weight logged!")
                st.rerun()
        if not profile or not plan:
            st.write("---")
            st.subheader("🚀 Set Up Your Plan")
            with st.form("onboarding"):
                height = st.number_input("Height (cm)", min_value=0, value=170)
                dob = st.date_input("Date of Birth", value=datetime(1990, 1, 1))
                gender = st.selectbox("Gender", ["male", "female", "other"])
                current_weight = st.number_input("Current Weight (kg)", min_value=0.0, value=70.0)
                target_weight = st.number_input("Target Weight (kg)", min_value=0.0, value=65.0)
                weekly_loss = st.number_input("Weekly Loss (kg)", min_value=0.0, max_value=1.0, value=0.5, step=0.25)
                if st.form_submit_button("Create Plan"):
                    age = datetime.now().year - dob.year
                    if gender == "male":
                        bmr = 10 * current_weight + 6.25 * height - 5 * age + 5
                    else:
                        bmr = 10 * current_weight + 6.25 * height - 5 * age - 161
                    tdee = int(bmr * 1.2)
                    daily_target = int(tdee - (weekly_loss * 7700 / 7))
                    supabase.table("profiles").insert({"id": st.session_state.user_id, "height_cm": height, "date_of_birth": dob.isoformat(), "gender": gender, "baseline_tdee": tdee}).execute()
                    supabase.table("weight_plans").insert({"user_id": st.session_state.user_id, "start_weight_kg": current_weight, "target_weight_kg": target_weight, "weekly_loss_rate_kg": weekly_loss, "daily_kcal_target": daily_target}).execute()
                    st.success(f"Plan created! Daily target: {daily_target} kcal")
                    st.rerun()
        st.write("---")
        if st.button("Sign Out"):
            supabase.auth.sign_out()
            st.session_state.logged_in = False
            st.session_state.user_id = None
            st.rerun()
