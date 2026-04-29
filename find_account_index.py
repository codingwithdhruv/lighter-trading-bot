import sys
import os
import requests
from dotenv import load_dotenv

def main():
    load_dotenv()
    
    # Can accept L1 address or L1 Private key as argument
    input_val = sys.argv[1] if len(sys.argv) > 2 or (len(sys.argv) == 2 and sys.argv[1] not in ["--help", "-h"]) else None
    
    if not input_val:
        print("Usage: python3 find_account_index.py <L1_ADDRESS_OR_L1_PRIVATE_KEY>")
        print("\nExample: python3 find_account_index.py 0x20A9CbE6e04f749Ab126c654aCf84B5F89CFe106")
        sys.exit(1)
        
    address = input_val
    
    # If it looks like a private key (64 hex or 66 with 0x)
    if len(input_val) in [64, 66] and not input_val.startswith("0x0"):
        try:
            from eth_account import Account
            address = Account.from_key(input_val).address
            print(f"Derived L1 Address from Private Key: {address}")
        except Exception as e:
            print(f"Error parsing private key: {e}")
            sys.exit(1)
            
    print(f"Querying Lighter accounts for address: {address}...\n")
    
    url = f"https://mainnet.zklighter.elliot.ai/api/v1/accountsByL1Address?l1_address={address}"
    try:
        resp = requests.get(url, headers={'User-Agent': 'Mozilla/5.0'})
        if resp.status_code != 200:
            print(f"Error from Lighter API: {resp.status_code} - {resp.text}")
            sys.exit(1)
            
        data = resp.json()
        sub_accounts = data.get("sub_accounts", [])
        
        if not sub_accounts:
            print("No Lighter accounts found for this address.")
            sys.exit(0)
            
        print(f"Found {len(sub_accounts)} account(s):")
        print("=" * 40)
        for acc in sub_accounts:
            print(f"Account Index : {acc.get('index')}")
            print(f"Account Type  : {'Main' if acc.get('account_type') == 0 else 'Sub-Account'}")
            print(f"Collateral    : {acc.get('collateral')} USDC")
            print("-" * 40)
            
    except Exception as e:
        print(f"Request failed: {e}")
        sys.exit(1)

if __name__ == "__main__":
    main()
