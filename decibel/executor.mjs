/**
 * Decibel DEX Executor Sidecar
 * 
 * Invoked by the Python bot via subprocess.
 * Reads a single JSON command from stdin, executes it, and writes JSON result to stdout.
 * 
 * Commands:
 *   { "cmd": "place_order", "args": { marketName, price, size, isBuy, tpTriggerPrice?, tpLimitPrice?, slTriggerPrice?, slLimitPrice?, subaccountAddr? } }
 *   { "cmd": "place_tpsl",  "args": { marketAddr, tpTriggerPrice?, tpLimitPrice?, tpSize?, slTriggerPrice?, slLimitPrice?, slSize?, subaccountAddr? } }
 *   { "cmd": "get_markets" }
 *   { "cmd": "get_subaccount" }
 * 
 * Environment:
 *   DECIBEL_PRIVATE_KEY   - Ed25519 private key hex (API Wallet, NOT main wallet)
 *   DECIBEL_NODE_API_KEY  - Geomi Bearer token for fullnode auth
 */

import {
  DecibelReadDex,
  DecibelWriteDex,
  MAINNET_CONFIG,
  GasPriceManager,
  TimeInForce,
  getPrimarySubaccountAddr,
} from "@decibeltrade/sdk";
import { Ed25519Account, Ed25519PrivateKey } from "@aptos-labs/ts-sdk";

// ── Helpers ──────────────────────────────────────────────────────────────────

function readStdin() {
  return new Promise((resolve, reject) => {
    let data = "";
    process.stdin.setEncoding("utf8");
    process.stdin.on("data", (chunk) => (data += chunk));
    process.stdin.on("end", () => {
      try {
        resolve(JSON.parse(data));
      } catch (e) {
        reject(new Error(`Invalid JSON on stdin: ${e.message}`));
      }
    });
    process.stdin.on("error", reject);
  });
}

function reply(obj) {
  process.stdout.write(JSON.stringify(obj) + "\n");
}

// ── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  const PRIVATE_KEY = process.env.DECIBEL_PRIVATE_KEY;
  const NODE_API_KEY = process.env.DECIBEL_NODE_API_KEY;

  if (!PRIVATE_KEY || !NODE_API_KEY) {
    reply({ success: false, error: "Missing DECIBEL_PRIVATE_KEY or DECIBEL_NODE_API_KEY" });
    process.exit(1);
  }

  // Initialize SDK
  // Handle AIP-80 format: "ed25519-priv-0x<hex>" → "0x<hex>"
  let keyHex = PRIVATE_KEY;
  if (keyHex.startsWith("ed25519-priv-")) {
    keyHex = keyHex.replace("ed25519-priv-", "");
  }
  const account = new Ed25519Account({
    privateKey: new Ed25519PrivateKey(keyHex),
  });

  const gas = new GasPriceManager(MAINNET_CONFIG);
  await gas.initialize();

  const read = new DecibelReadDex(MAINNET_CONFIG, {
    nodeApiKey: NODE_API_KEY,
  });

  const write = new DecibelWriteDex(MAINNET_CONFIG, account, {
    nodeApiKey: NODE_API_KEY,
    gasPriceManager: gas,
    skipSimulate: false,
  });

  // Read command from stdin
  let input;
  try {
    input = await readStdin();
  } catch (e) {
    reply({ success: false, error: e.message });
    process.exit(1);
  }

  const { cmd, args = {} } = input;

  try {
    switch (cmd) {
      // ── Get Markets ──────────────────────────────────────────────────────
      case "get_markets": {
        const markets = await read.getMarkets();
        const simplified = markets.map((m) => ({
          market_name: m.market_name,
          market_addr: m.market_addr,
          px_decimals: m.px_decimals,
          sz_decimals: m.sz_decimals,
          tick_size: m.tick_size,
          lot_size: m.lot_size,
          min_size: m.min_size,
          max_leverage: m.max_leverage,
        }));
        reply({ success: true, data: simplified });
        break;
      }

      // ── Get Subaccount Address ─────────────────────────────────────────
      case "get_subaccount": {
        let subAddr = args.subaccountAddr || process.env.DECIBEL_SUBACCOUNT || "";
        if (!subAddr) {
          try {
            subAddr = getPrimarySubaccountAddr(account.accountAddress).toString();
          } catch {
            subAddr = account.accountAddress.toString();
          }
        }
        reply({ success: true, data: { subaccount: subAddr, owner: account.accountAddress.toString() } });
        break;
      }

      // ── Place Order ────────────────────────────────────────────────────
      case "place_order": {
        const {
          marketName,
          price,
          size,
          isBuy,
          tpTriggerPrice,
          tpLimitPrice,
          slTriggerPrice,
          slLimitPrice,
          subaccountAddr,
        } = args;

        if (!marketName || price == null || size == null || isBuy == null) {
          reply({ success: false, error: "place_order requires marketName, price, size, isBuy" });
          break;
        }

        const orderArgs = {
          marketName,
          price: Number(price),
          size: Number(size),
          isBuy: Boolean(isBuy),
          timeInForce: TimeInForce.ImmediateOrCancel,  // Market-like behavior
          isReduceOnly: false,
          clientOrderId: `lighter-bot-${Date.now()}`,
        };

        // Attach TP/SL if provided (inline with the order)
        if (tpTriggerPrice != null) orderArgs.tpTriggerPrice = Number(tpTriggerPrice);
        if (tpLimitPrice != null) orderArgs.tpLimitPrice = Number(tpLimitPrice);
        if (slTriggerPrice != null) orderArgs.slTriggerPrice = Number(slTriggerPrice);
        if (slLimitPrice != null) orderArgs.slLimitPrice = Number(slLimitPrice);
        if (subaccountAddr) orderArgs.subaccountAddr = subaccountAddr;

        const result = await write.placeOrder(orderArgs);

        reply({
          success: result.success,
          orderId: result.orderId || null,
          transactionHash: result.transactionHash || null,
        });
        break;
      }

      // ── Place TP/SL for Existing Position ──────────────────────────────
      case "place_tpsl": {
        const {
          marketAddr,
          tpTriggerPrice: tp_trigger,
          tpLimitPrice: tp_limit,
          tpSize,
          slTriggerPrice: sl_trigger,
          slLimitPrice: sl_limit,
          slSize,
          subaccountAddr: subAddr,
        } = args;

        if (!marketAddr) {
          reply({ success: false, error: "place_tpsl requires marketAddr" });
          break;
        }

        const tpslArgs = { marketAddr };
        if (tp_trigger != null) tpslArgs.tpTriggerPrice = Number(tp_trigger);
        if (tp_limit != null) tpslArgs.tpLimitPrice = Number(tp_limit);
        if (tpSize != null) tpslArgs.tpSize = Number(tpSize);
        if (sl_trigger != null) tpslArgs.slTriggerPrice = Number(sl_trigger);
        if (sl_limit != null) tpslArgs.slLimitPrice = Number(sl_limit);
        if (slSize != null) tpslArgs.slSize = Number(slSize);
        if (subAddr) tpslArgs.subaccountAddr = subAddr;

        const tpslResult = await write.placeTpSlOrderForPosition(tpslArgs);
        reply({
          success: true,
          transactionHash: tpslResult.hash || null,
        });
        break;
      }

      default:
        reply({ success: false, error: `Unknown command: ${cmd}` });
    }
  } catch (err) {
    reply({ success: false, error: err.message || String(err) });
  }

  process.exit(0);
}

main();
