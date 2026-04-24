/**
 * Decibel DEX Executor Sidecar
 * 
 * Invoked by the Python bot via subprocess.
 * Reads a single JSON command from stdin, executes it, and writes JSON result to stdout.
 * 
 * Commands:
 *   { "cmd": "place_order", "args": { marketName, price, size, isBuy, tpTriggerPrice?, tpLimitPrice?, slTriggerPrice?, slLimitPrice?, subaccountAddr? } }
 *   { "cmd": "place_tpsl",  "args": { marketAddr, tpTriggerPrice?, tpLimitPrice?, tpSize?, slTriggerPrice?, slLimitPrice?, slSize?, subaccountAddr? } }
 *   { "cmd": "cancel_order", "args": { orderId, marketName? } }
 *   { "cmd": "get_markets" }
 *   { "cmd": "get_subaccount" }
 *   { "cmd": "diagnose" }
 * 
 * Environment:
 *   DECIBEL_PRIVATE_KEY      - API Wallet private key (AIP-80 ed25519-priv-0x... or raw 0x hex)
 *   DECIBEL_NODE_API_KEY     - Geomi Client API key (Bearer token for fullnode auth + rate limits)
 *   DECIBEL_SUBACCOUNT       - Trading Account (subaccount) address on Decibel
 *   DECIBEL_GAS_STATION_KEY  - (Optional) Geomi Gas Station API key for sponsored gas.
 *                              If set, transactions are gas-free (no APT needed).
 *                              If not set, APT must be deposited to the API Wallet address.
 * 
 * Credential model (Decibel three-tier):
 *   1. Primary Wallet  — your main wallet (used to create API Wallet on app.decibel.trade/api)
 *   2. API Wallet      — derived from DECIBEL_PRIVATE_KEY. Signs all transactions. Needs APT for gas
 *                         (unless Gas Station is enabled). This is NOT the trading account.
 *   3. Trading Account — DECIBEL_SUBACCOUNT. Holds USDC collateral. Created from API Wallet.
 * 
 * Both DECIBEL_NODE_API_KEY and DECIBEL_GAS_STATION_KEY come from Geomi (https://geomi.dev).
 *   - Node API Key: Required. Created under "API Keys" resource. Used for fullnode rate limiting.
 *   - Gas Station Key: Optional. Created under "Gas Station" resource. Sponsors tx gas fees.
 */

import {
  DecibelReadDex,
  DecibelWriteDex,
  MAINNET_CONFIG,
  GasPriceManager,
  TimeInForce,
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

/**
 * Parse private key from env. Accepts both formats:
 *   - AIP-80:  "ed25519-priv-0x<64hex>"   (from Decibel app "Create API Wallet")
 *   - Raw hex: "0x<64hex>"
 * 
 * The Aptos SDK's Ed25519PrivateKey constructor accepts BOTH formats natively.
 * We do NOT strip the "ed25519-priv-" prefix — the SDK handles AIP-80 internally.
 * Stripping it actually CAUSES the SDK to emit a deprecation warning.
 */
function parsePrivateKey(rawKey) {
  const trimmed = rawKey.trim();
  // Let the SDK handle format detection natively — it supports both AIP-80 and raw hex
  return new Ed25519PrivateKey(trimmed);
}

// ── Main ─────────────────────────────────────────────────────────────────────

async function main() {
  const PRIVATE_KEY = process.env.DECIBEL_PRIVATE_KEY;
  const NODE_API_KEY = process.env.DECIBEL_NODE_API_KEY;

  if (!PRIVATE_KEY || !NODE_API_KEY) {
    reply({ success: false, error: "Missing DECIBEL_PRIVATE_KEY or DECIBEL_NODE_API_KEY" });
    process.exit(1);
  }

  // Parse private key (SDK handles AIP-80 format natively)
  const privKey = parsePrivateKey(PRIVATE_KEY);
  const account = new Ed25519Account({ privateKey: privKey });
  const signerAddress = account.accountAddress.toString();

  // Build config with optional Gas Station
  let config = { ...MAINNET_CONFIG };
  const GAS_STATION_KEY = process.env.DECIBEL_GAS_STATION_KEY;
  if (GAS_STATION_KEY) {
    config.gasStationApiKey = GAS_STATION_KEY;
  }

  // Log diagnostics to stderr (visible in PM2 logs, not in stdout JSON)
  const envSubaccount = process.env.DECIBEL_SUBACCOUNT || "";
  process.stderr.write(`[Decibel] API Wallet (signer): ${signerAddress}\n`);
  process.stderr.write(`[Decibel] Env DECIBEL_SUBACCOUNT: ${envSubaccount || "(not set)"}\n`);
  process.stderr.write(`[Decibel] Gas Station: ${GAS_STATION_KEY ? "ENABLED" : "DISABLED — APT needed at " + signerAddress}\n`);

  const gas = new GasPriceManager(config);
  await gas.initialize();

  const read = new DecibelReadDex(config, {
    nodeApiKey: NODE_API_KEY,
  });

  const write = new DecibelWriteDex(config, account, {
    nodeApiKey: NODE_API_KEY,
    gasPriceManager: gas,
    skipSimulate: false,
  });

  // Derive primary subaccount using the SDK's own method (correctly passes package address)
  let derivedSubaccount = "";
  try {
    derivedSubaccount = write.getPrimarySubaccountAddress(account.accountAddress);
  } catch (e) {
    process.stderr.write(`[Decibel] Warning: Could not derive primary subaccount: ${e.message}\n`);
  }
  if (derivedSubaccount) {
    process.stderr.write(`[Decibel] Primary subaccount (derived): ${derivedSubaccount}\n`);
  }

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
        const markets = await read.markets.getAll();
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
        const subAddr = args.subaccountAddr || envSubaccount || derivedSubaccount || "";
        reply({
          success: true,
          data: {
            subaccount: subAddr,
            owner: signerAddress,
            derivedPrimary: derivedSubaccount,
          },
        });
        break;
      }

      // ── Diagnose — full credential check ───────────────────────────────
      case "diagnose": {
        const diagSubaccount = envSubaccount || derivedSubaccount || "";
        let balanceInfo = null;
        if (diagSubaccount) {
          try {
            const overview = await read.accountOverview.getByAddr(diagSubaccount);
            balanceInfo = overview;
          } catch (e) {
            balanceInfo = { error: e.message };
          }
        }
        reply({
          success: true,
          data: {
            signerAddress,
            derivedPrimary: derivedSubaccount,
            envSubaccount: envSubaccount || "(not set)",
            gasStationEnabled: !!GAS_STATION_KEY,
            keyFormat: PRIVATE_KEY.startsWith("ed25519-priv-") ? "AIP-80" : "raw hex",
            balanceInfo,
          },
        });
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
          timeInForce: args.timeInForce !== undefined ? Number(args.timeInForce) : TimeInForce.ImmediateOrCancel,
          isReduceOnly: Boolean(args.isReduceOnly || false),
          clientOrderId: `lighter-bot-${Date.now()}`,
        };

        // Attach TP/SL if provided (inline with the order)
        if (tpTriggerPrice != null) orderArgs.tpTriggerPrice = Number(tpTriggerPrice);
        if (tpLimitPrice != null) orderArgs.tpLimitPrice = Number(tpLimitPrice);
        if (slTriggerPrice != null) orderArgs.slTriggerPrice = Number(slTriggerPrice);
        if (slLimitPrice != null) orderArgs.slLimitPrice = Number(slLimitPrice);
        // Pass subaccount: explicit arg > env var (SDK auto-derives if omitted)
        if (subaccountAddr) orderArgs.subaccountAddr = subaccountAddr;

        process.stderr.write(`[Decibel] Placing order: ${JSON.stringify(orderArgs)}\n`);

        const result = await write.placeOrder(orderArgs);

        // Log the full result for debugging
        process.stderr.write(`[Decibel] placeOrder result: ${JSON.stringify(result)}\n`);

        reply({
          success: result.success === true || !!(result.transactionHash || result.hash),
          orderId: result.orderId || null,
          transactionHash: result.transactionHash || result.hash || null,
          error: result.error || null,
        });
        break;
      }

      // ── Cancel Order ───────────────────────────────────────────────────
      case "cancel_order": {
        const { orderId, marketName: cancelMarketName, subaccountAddr: cancelSubAddr } = args;
        if (!orderId) {
          reply({ success: false, error: "cancel_order requires orderId" });
          break;
        }
        
        const cancelArgs = { orderId: Number(orderId) };
        if (cancelMarketName) cancelArgs.marketName = cancelMarketName;
        if (cancelSubAddr) cancelArgs.subaccountAddr = cancelSubAddr;
        
        const result = await write.cancelOrder(cancelArgs);
        reply({
          success: result.success === true || !!(result.transactionHash || result.hash),
          transactionHash: result.transactionHash || result.hash || null,
          error: result.error || null,
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
          transactionHash: tpslResult.transactionHash || tpslResult.hash || null,
        });
        break;
      }

      default:
        reply({ success: false, error: `Unknown command: ${cmd}` });
    }
  } catch (err) {
    // Surface the full error for debugging
    process.stderr.write(`[Decibel] Error in ${cmd}: ${err.stack || err.message || String(err)}\n`);
    reply({ success: false, error: err.message || String(err) });
  }

  process.exit(0);
}

main();
