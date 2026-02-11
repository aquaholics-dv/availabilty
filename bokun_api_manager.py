"""
Aquaholics Boat Booking App
Uses the correct REST v2 /experience/{id}/components endpoint
to set availability rules properly via API
"""

from flask import Flask, render_template_string, request, jsonify
from flask_cors import CORS
import requests
import base64
import hashlib
import hmac
import os
from datetime import datetime, timezone
from dotenv import load_dotenv

load_dotenv()

app = Flask(__name__)
CORS(app)

BOKUN_ACCESS_KEY = os.getenv('BOKUN_ACCESS_KEY', 'b048bb24bc604475aaa503ac29f9caae')
BOKUN_SECRET_KEY = os.getenv('BOKUN_SECRET_KEY', '0bd28b4cff1340749168428d675f6b2a')
BOKUN_BASE_URL = 'https://api.bokun.io'

def get_bokun_headers(method, path):
    """Generate HMAC-SHA1 auth headers for Bokun API"""
    date_str = datetime.now(timezone.utc).strftime('%Y-%m-%d %H:%M:%S')
    signature_string = f"{date_str}{BOKUN_ACCESS_KEY}{method.upper()}{path}"
    raw_sig = hmac.new(
        BOKUN_SECRET_KEY.encode(),
        signature_string.encode(),
        hashlib.sha1
    ).digest()
    signature = base64.b64encode(raw_sig).decode("utf-8")
    return {
        'X-Bokun-Date': date_str,
        'X-Bokun-AccessKey': BOKUN_ACCESS_KEY,
        'X-Bokun-Signature': signature,
        'Content-Type': 'application/json'
    }

def bokun_get(path):
    """GET request to Bokun API"""
    url = f'{BOKUN_BASE_URL}{path}'
    headers = get_bokun_headers('GET', path)
    response = requests.get(url, headers=headers)
    return response

def bokun_put(path, payload):
    """PUT request to Bokun API"""
    url = f'{BOKUN_BASE_URL}{path}'
    headers = get_bokun_headers('PUT', path)
    response = requests.put(url, json=payload, headers=headers)
    return response

def bokun_post(path, payload):
    """POST request to Bokun API"""
    url = f'{BOKUN_BASE_URL}{path}'
    headers = get_bokun_headers('POST', path)
    response = requests.post(url, json=payload, headers=headers)
    return response

@app.route('/')
def index():
    return render_template_string(HTML_TEMPLATE)

@app.route('/api/experiences', methods=['GET'])
def get_experiences():
    """Fetch specific experiences by ID"""
    try:
        experience_ids = [
            1084194, 1087988, 1088027, 1113923, 1113953,
            1113957, 1113944, 1113948, 1124650, 1111734
        ]
        all_items = []
        for eid in experience_ids:
            resp = bokun_get(f'/activity.json/{eid}')
            if resp.status_code == 200:
                e = resp.json()
                all_items.append({'id': e['id'], 'title': e['title']})
                print(f'  [{e["id"]}] {e["title"]}')
            else:
                print(f'  [{eid}] ERROR {resp.status_code}: {resp.text[:100]}')
        print(f'\nLoaded {len(all_items)} experiences')
        return jsonify({'success': True, 'experiences': all_items})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/get-start-times/<int:experience_id>', methods=['GET'])
def get_start_times(experience_id):
    """Get start times for an experience by checking multiple component types"""
    try:
        components_to_try = ['RATES', 'DEFAULT_OPENING_HOURS', 'BOOKING_TYPE']
        for comp in components_to_try:
            path = f'/restapi/v2.0/experience/{experience_id}/components?componentType={comp}'
            response = bokun_get(path)
            print(f'{comp} -> {response.status_code}: {response.text[:300]}')

        # The start times with IDs live in the activity detail endpoint (v1)
        path_v1 = f'/activity.json/{experience_id}'
        response_v1 = bokun_get(path_v1)
        print(f'Activity detail {response_v1.status_code}: {response_v1.text[:500]}')
        if response_v1.status_code == 200:
            data = response_v1.json()
            start_times = data.get('startTimes', data.get('departureTimes', []))
            print(f'Start times from activity detail: {start_times}')
            return jsonify({'success': True, 'startTimes': start_times, 'raw': data.get('startTimes', [])})

        return jsonify({'success': True, 'startTimes': []})
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/get-availability-rules/<int:experience_id>', methods=['GET'])
def get_availability_rules(experience_id):
    """Get current availability rules for an experience"""
    try:
        path = f'/restapi/v2.0/experience/{experience_id}/components?componentType=AVAILABILITY_RULES'
        response = bokun_get(path)

        if response.status_code == 200:
            data = response.json()
            rules = data.get('availabilityRules', [])

            # Fetch booking type separately - AVAILABILITY_RULES component doesn't include it
            bt_resp = bokun_get(f'/restapi/v2.0/experience/{experience_id}/components?componentType=BOOKING_TYPE')
            if bt_resp.status_code == 200:
                booking_type = bt_resp.json().get('bookingType', 'DATE_ONLY')
            else:
                booking_type = 'DATE_ONLY'

            # Fetch start times from activity detail (v1) - this has the real time IDs and labels
            start_times = []
            if booking_type == 'DATE_AND_TIME':
                detail_resp = bokun_get(f'/activity.json/{experience_id}')
                if detail_resp.status_code == 200:
                    detail = detail_resp.json()
                    raw_times = detail.get('startTimes', [])
                    start_times = [{
                        'id':    st['id'],
                        'label': f"{str(st['hour']).zfill(2)}:{str(st['minute']).zfill(2)}"
                    } for st in raw_times]

            print(f'Booking type: {booking_type}')
            print(f'Start times: {start_times}')
            return jsonify({
                'success': True,
                'rules': rules,
                'bookingType': booking_type,
                'startTimes': start_times
            })
        return jsonify({'success': False, 'error': f'{response.status_code}: {response.text}'}), 400
    except Exception as e:
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/add-availability-rule', methods=['POST'])
def add_availability_rule():
    """
    Add a new availability rule for a single specific date.
    Fetches existing rules first, then appends the new one.
    """
    try:
        data = request.json
        experience_id  = data.get('experience_id')
        date           = data.get('date')              # YYYY-MM-DD single date
        capacity       = data.get('capacity', 12)
        booking_type   = data.get('booking_type', 'DATE_ONLY')
        start_time_ids = data.get('start_time_ids', [])
        all_start_times = data.get('all_start_times', True)
        # For a single date, start and end are the same day
        start_date     = date
        end_date       = date
        weekdays       = []
        months         = []

        # Step 1: Fetch existing components so we keep everything intact
        path = f'/restapi/v2.0/experience/{experience_id}/components?componentType=AVAILABILITY_RULES'
        get_resp = bokun_get(path)

        if get_resp.status_code != 200:
            return jsonify({
                'success': False,
                'error': f'Could not fetch existing rules: {get_resp.status_code} {get_resp.text}'
            }), 400

        existing = get_resp.json()
        existing_rules = existing.get('availabilityRules', [])
        booking_type   = existing.get('bookingType', 'DATE_ONLY')
        print(f'Existing rules from API: {existing_rules}')

        # Step 2: Build the new rule
        recurrence_rule = {
            'startDate': start_date,
            'endDate':   end_date,
        }
        if weekdays:
            recurrence_rule['byWeekday'] = weekdays
        if months:
            recurrence_rule['byMonth'] = months

        new_rule = {
            # No 'id' field = create new rule
            'recurrenceRule':       recurrence_rule,
            'maxCapacity':          capacity,
            'maxCapacityForPickup': capacity,
            'minTotalPax':          1,
            'allStartTimes':        True,
            # startTimes must NOT be sent when allStartTimes is True
            'guidedLanguages':      [],
        }

        # Only send start time fields for DATE_AND_TIME products
        if booking_type == 'DATE_AND_TIME':
            if all_start_times:
                new_rule['allStartTimes'] = True
            else:
                new_rule['allStartTimes'] = False
                new_rule['startTimes'] = [{'id': sid} for sid in start_time_ids]

        # Step 3: Clean existing rules before sending back
        clean_existing = []
        for rule in existing_rules:
            # Fix maxCapacityForPickup - must be >= 1
            if not rule.get('maxCapacityForPickup') or rule['maxCapacityForPickup'] < 1:
                rule['maxCapacityForPickup'] = rule.get('maxCapacity', 12)
            # Clean startTimes - remove externalId to avoid id/externalId conflict
            if rule.get('startTimes'):
                rule['startTimes'] = [{'id': st['id']} for st in rule['startTimes'] if st.get('id')]
            clean_existing.append(rule)

        updated_rules = clean_existing + [new_rule]

        put_payload = {
            'availabilityRules': updated_rules
        }

        put_resp = bokun_put(path, put_payload)

        if put_resp.status_code == 200:
            result = put_resp.json()
            saved_rules = result.get('availabilityRules', [])
            return jsonify({
                'success': True,
                'message': f'Availability rule added! Total rules: {len(saved_rules)}',
                'rules': saved_rules
            })
        else:
            return jsonify({
                'success': False,
                'error': f'{put_resp.status_code}: {put_resp.text}'
            }), 400

    except Exception as e:
        import traceback
        return jsonify({'success': False, 'error': str(e), 'trace': traceback.format_exc()}), 500


HTML_TEMPLATE = '''
<!DOCTYPE html>
<html>
<head>
    <title>Aquaholics Boat Booking App</title>
    <style>
        * { box-sizing: border-box; margin: 0; padding: 0; }
        body {
            font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Arial, sans-serif;
            background: linear-gradient(135deg, #1d57c7 0%, #0a2d6e 100%);
            min-height: 100vh;
            padding: 30px 20px;
        }
        .container { max-width: 850px; margin: 0 auto; }
        .card {
            background: white;
            border-radius: 16px;
            padding: 30px;
            margin-bottom: 20px;
            box-shadow: 0 4px 20px rgba(0,0,0,0.15);
        }
        h1 { color: white; font-size: 30px; margin-bottom: 6px; }
        .tagline { color: rgba(255,255,255,0.8); margin-bottom: 25px; font-size: 15px; }
        h2 { font-size: 18px; color: #1d57c7; margin-bottom: 18px; }
        .form-row { display: grid; grid-template-columns: 1fr 1fr; gap: 15px; }
        .form-group { margin-bottom: 16px; }
        label { display: block; font-size: 13px; font-weight: 600; color: #444; margin-bottom: 5px; }
        input, select { width: 100%; padding: 11px 14px; border: 2px solid #e2e8f0; border-radius: 8px; font-size: 14px; }
        input:focus, select:focus { outline: none; border-color: #1d57c7; }
        .days-grid { display: grid; grid-template-columns: repeat(7, 1fr); gap: 6px; }
        .day-btn {
            padding: 8px 4px; border: 2px solid #e2e8f0; border-radius: 8px;
            background: white; font-size: 12px; font-weight: 600; cursor: pointer;
            text-align: center; transition: all 0.2s; color: #555;
        }
        .day-btn.active { background: #1d57c7; color: white; border-color: #1d57c7; }
        .btn {
            width: 100%; padding: 14px; border: none; border-radius: 10px;
            font-size: 16px; font-weight: 700; cursor: pointer; transition: all 0.2s; margin-top: 8px;
        }
        .btn-primary { background: #1d57c7; color: white; }
        .btn-primary:hover { background: #164aab; transform: translateY(-1px); }
        .btn-secondary { background: #f1f5f9; color: #444; margin-top: 10px; }
        .status { padding: 14px 18px; border-radius: 10px; margin-top: 16px; font-size: 14px; display: none; }
        .status.show { display: block; }
        .status.success { background: #d1fae5; border-left: 4px solid #10b981; color: #065f46; }
        .status.error   { background: #fee2e2; border-left: 4px solid #ef4444; color: #7f1d1d; }
        .status.info    { background: #dbeafe; border-left: 4px solid #3b82f6; color: #1e3a8a; }
        .rules-list { margin-top: 16px; }
        .rule-item {
            background: #f8fafc; border: 1px solid #e2e8f0; border-radius: 8px;
            padding: 12px 16px; margin-bottom: 10px; font-size: 13px; color: #444;
        }
        .rule-item strong { color: #1d57c7; }
        .section-label { font-size: 13px; font-weight: 600; color: #444; margin-bottom: 8px; }
        @media (max-width: 600px) { .form-row { grid-template-columns: 1fr; } }
    </style>
</head>
<body>
<div class="container">
    <h1>🌊 Aquaholics Boat Booking App</h1>

    <!-- Experience Selector -->
    <div class="card">
        <h2>1. Select Experience</h2>
        <div class="form-group">
            <label>Experience</label>
            <select id="experience" onchange="loadRules()">
                <option value="">Loading experiences...</option>
            </select>
        </div>
        <div id="rulesSection" style="display:none">
            <div onclick="toggleRules()" style="cursor:pointer;display:flex;align-items:center;
                justify-content:space-between;padding:10px 14px;background:#f0f4ff;
                border-radius:8px;border:1px solid #c7d7f9;margin-top:8px">
                <span style="font-weight:600;color:#1d57c7;font-size:14px">📋 Current Availability</span>
                <span id="rulesToggleIcon" style="color:#1d57c7;font-size:18px;transition:transform 0.2s">▼</span>
            </div>
            <div id="rulesList" class="rules-list" style="display:none;margin-top:4px"></div>
        </div>
    </div>

    <!-- Add Availability -->
    <div class="card">
        <h2>2. Add Availability for a Date</h2>
        <div class="form-group">
            <label>Select Date</label>
            <input type="date" id="date">
        </div>
        <div class="form-group" id="timeGroup" style="display:none">
            <label>Start Times</label>
            <div id="startTimesList"></div>
            <div id="noTimesMsg" style="display:none;color:#e67e22;font-size:13px;padding:8px;background:#fef9e7;border-radius:6px;margin-top:8px">
                ⚠️ No start times configured on this experience in Bokun yet.
            </div>
        </div>
        <div class="form-group">
            <label>Capacity</label>
            <input type="number" id="capacity" value="12" min="1">
        </div>
        <button class="btn btn-primary" onclick="addRule()">➕ Add This Date</button>
        <div id="status" class="status"></div>
    </div>

    <!-- Added Dates Log -->
    <div class="card" id="logCard" style="display:none">
        <h2>✅ Dates Added This Session</h2>
        <div id="addedLog"></div>
    </div>
</div>

<script>
    let bookingType = 'DATE_ONLY';
    let addedDates  = [];

    async function loadExperiences() {
        const resp = await fetch('/api/experiences');
        const data = await resp.json();
        const sel  = document.getElementById('experience');
        if (data.success) {
            sel.innerHTML = '<option value="">-- Select an experience --</option>';
            data.experiences.forEach(e => {
                const opt = document.createElement('option');
                opt.value = e.id;
                opt.textContent = e.title;
                sel.appendChild(opt);
            });
        }
    }

    async function loadRules() {
        const id = document.getElementById('experience').value;
        if (!id) return;

        addedDates = [];
        document.getElementById('logCard').style.display = 'none';
        document.getElementById('addedLog').innerHTML = '';

        showStatus('Loading current availability...', 'info');
        const resp = await fetch(`/api/get-availability-rules/${id}`);
        const data = await resp.json();

        const section = document.getElementById('rulesSection');
        const list    = document.getElementById('rulesList');
        section.style.display = 'block';

        if (data.success) {
            bookingType = data.bookingType || 'DATE_ONLY';

            // Handle start times display
            const timeGroup    = document.getElementById('timeGroup');
            const timesList    = document.getElementById('startTimesList');
            const noTimesMsg   = document.getElementById('noTimesMsg');

            if (bookingType === 'DATE_AND_TIME') {
                timeGroup.style.display = 'block';
                if (data.startTimes && data.startTimes.length > 0) {
                    noTimesMsg.style.display = 'none';
                    timesList.innerHTML = data.startTimes.map(st =>
                        `<label style="display:flex;align-items:center;gap:10px;padding:8px 12px;margin:4px 0;
                            background:#f8f9fa;border-radius:8px;cursor:pointer;font-size:15px;border:1px solid #e2e8f0">
                            <input type="checkbox" value="${st.id}" style="width:17px;height:17px;cursor:pointer">
                            <span style="font-weight:700;color:#1d57c7;font-size:16px">${st.label}</span>
                        </label>`
                    ).join('');
                } else {
                    timesList.innerHTML = '';
                    noTimesMsg.style.display = 'block';
                }
            } else {
                timeGroup.style.display = 'none';
                timesList.innerHTML = '';
            }

            // Show existing rules
            if (data.rules.length === 0) {
                list.innerHTML = '<div class="rule-item">No availability dates yet.</div>';
            } else {
                list.innerHTML = data.rules.map(r => {
                    const start = r.recurrenceRule?.startDate || '?';
                    const end   = r.recurrenceRule?.endDate   || '?';
                    const label = start === end ? `📅 ${start}` : `📅 ${start} → ${end}`;
                    return `<div class="rule-item">
                        ${label} &nbsp;|&nbsp; Capacity: <strong>${r.maxCapacity}</strong>
                        ${r.recurrenceRule?.byWeekday?.length ? `&nbsp;|&nbsp; ${r.recurrenceRule.byWeekday.join(', ')}` : ''}
                    </div>`;
                }).join('');
            }
            hideStatus();
        } else {
            list.innerHTML = `<div class="rule-item" style="color:#ef4444">${data.error}</div>`;
            hideStatus();
        }
    }

    async function addRule() {
        const experienceId = document.getElementById('experience').value;
        const date         = document.getElementById('date').value;
        const capacity     = parseInt(document.getElementById('capacity').value);

        if (!experienceId) return showStatus('Please select an experience', 'error');
        if (!date)         return showStatus('Please select a date', 'error');

        // Collect checked start times for DATE_AND_TIME experiences
        const checkedBoxes    = [...document.querySelectorAll('#startTimesList input[type=checkbox]:checked')];
        const selectedTimeIds = checkedBoxes.map(cb => parseInt(cb.value));
        const selectedLabels  = checkedBoxes.map(cb => cb.nextElementSibling.textContent.trim());

        if (bookingType === 'DATE_AND_TIME' && selectedTimeIds.length === 0) {
            return showStatus('Please select at least one start time', 'error');
        }

        showStatus('Adding date...', 'info');

        const resp = await fetch('/api/add-availability-rule', {
            method:  'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
                experience_id:   parseInt(experienceId),
                date:            date,
                capacity:        capacity,
                booking_type:    bookingType,
                start_time_ids:  selectedTimeIds,
                all_start_times: selectedTimeIds.length === 0,
            })
        });
        const data = await resp.json();

        if (data.success) {
            const timeLabel = selectedLabels.length ? ` at ${selectedLabels.join(', ')}` : '';
            showStatus(`✅ ${date}${timeLabel} added!`, 'success');
            addedDates.push({ date, timeLabel, capacity });
            document.getElementById('logCard').style.display = 'block';
            document.getElementById('addedLog').innerHTML = addedDates.map(d =>
                `<div class="rule-item">📅 <strong>${d.date}</strong>${d.timeLabel} &nbsp;|&nbsp; Capacity: <strong>${d.capacity}</strong></div>`
            ).join('');
            // Jump to next day
            const next = new Date(date);
            next.setDate(next.getDate() + 1);
            document.getElementById('date').value = next.toISOString().split('T')[0];
            loadRules();
        } else {
            showStatus('❌ ' + data.error, 'error');
        }
    }

    function showStatus(msg, type) {
        const s = document.getElementById('status');
        s.textContent = msg;
        s.className = `status show ${type}`;
    }
    function hideStatus() {
        document.getElementById('status').className = 'status';
    }

    function toggleRules() {
        const list = document.getElementById('rulesList');
        const icon = document.getElementById('rulesToggleIcon');
        const isHidden = list.style.display === 'none';
        list.style.display = isHidden ? 'block' : 'none';
        icon.textContent = isHidden ? '▲' : '▼';
    }

    document.getElementById('date').value = new Date().toISOString().split('T')[0];
    loadExperiences();
</script>
</body>
</html>
'''

if __name__ == '__main__':
    print("\n" + "="*60)
    print("🌊 BOKUN AVAILABILITY MANAGER - API VERSION")
    print("="*60)
    print("\n✅ Uses Bokun REST v2 API - no browser automation needed!")
    print("\n👉 Open your browser and go to:")
    print("\n   http://localhost:5000\n")
    print("="*60 + "\n")
    port = int(os.environ.get('PORT', 5000))
    app.run(debug=False, host='0.0.0.0', port=port)

