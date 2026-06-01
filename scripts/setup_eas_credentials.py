#!/usr/bin/env python3
import os
import sys
import time

os.environ['EAS_BUILD_NO_EXPO_GO_WARNING'] = 'true'
os.environ['EXPO_TOKEN'] = os.environ.get('EXPO_TOKEN', '')
os.chdir('/home/runner/workspace/mobile')

import pexpect

child = pexpect.spawn('npx eas-cli@latest credentials:configure-build --platform ios', timeout=120, encoding='utf-8')
child.logfile = sys.stdout

# Wait for profile selection
try:
    child.expect('Which build profile do you want to configure?', timeout=30)
    # Send down arrow twice + enter
    child.send('\x1b[B')
    time.sleep(0.5)
    child.send('\x1b[B')
    time.sleep(0.5)
    child.send('\n')

    # Wait for Apple login prompt
    child.expect('Do you want to log in to your Apple account?', timeout=30)
    child.send('Y\n')

    # Wait for Apple ID prompt
    child.expect('Apple ID:', timeout=30)
    apple_id = os.environ.get('APPLE_ID', '')
    child.send(apple_id + '\n')

    # Wait for password prompt
    child.expect('Password:', timeout=60)
    apple_password = os.environ.get('APPLE_PASSWORD', '')
    child.send(apple_password + '\n')
    time.sleep(5)

    # After password, Apple may take time to authenticate. 
    # Wait for any prompt or completion.
    index = child.expect([
        'Do you want to reuse these credentials?',
        'Do you want to let EAS manage',
        'Setup credentials',
        'Two-factor authentication',
        '6-digit code',
        'Verification code',
        'Provisioning Profile',
        'Certificate',
        'Generate',
        'Create',
        '✔',
        '✖',
        pexpect.EOF,
        pexpect.TIMEOUT
    ], timeout=300)

    print(f"\nMatched index: {index}", file=sys.stderr)

    if index == 0:
        child.send('Y\n')
    elif index == 1:
        child.send('Y\n')
    elif index == 2:
        pass
    elif index in [3, 4, 5]:
        # 2FA required - cannot automate
        print("\n\n2FA REQUIRED! Please run this command manually on your phone:", file=sys.stderr)
        print("cd /home/runner/workspace/mobile && npx eas-cli@latest credentials:configure-build --platform ios", file=sys.stderr)
        sys.exit(1)
    elif index == 12:
        print("\n\nEOF reached - process completed.", file=sys.stderr)
        sys.exit(0)
    elif index == 13:
        print("\n\nTIMEOUT - process may still be running or waiting.", file=sys.stderr)
        sys.exit(1)

    # Wait for completion after any interaction
    child.expect(pexpect.EOF, timeout=300)

except pexpect.TIMEOUT:
    print("\n\nTIMEOUT: The process took too long.", file=sys.stderr)
    sys.exit(1)
except pexpect.EOF:
    print("\n\nProcess finished.", file=sys.stderr)
    sys.exit(0)
except Exception as e:
    print(f"\n\nERROR: {e}", file=sys.stderr)
    sys.exit(1)
finally:
    child.close()
