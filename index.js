const express = require('express');
const TelegramBot = require('node-telegram-bot-api');
const axios = require('axios');

// Initialize Express for payment callbacks
const app = express();
app.use(express.json());

// Initialize Telegram Bot
const token = process.env.TELEGRAM_BOT_TOKEN;
const bot = new TelegramBot(token, { polling: true });

// Memory store to keep track of active user purchases (No DB required)
const userStates = {};

// Hardcoded Plan Details (Edit as desired)
const PLANS = {
  daily: { name: "Daily 5 Odds", amount: 500, tips: "⚽ TODAY'S SAFE 5 ODDS:\n\n1. Real Madrid vs Barca - Over 2.5 @1.75\n2. Man City vs Arsenal - Home Win @1.85\n3. Bayern vs Dortmund - BTTS @1.55\n\nTotal Odds: 5.01\nGood luck! 🍀" },
  vip: { name: "VIP Elite Club (Monthly)", amount: 3000, inviteLink: "https://t.me/+YourPrivateChannelInviteLinkHere" }
};

// Start Command
bot.onText(/\/start/, (msg) => {
  const chatId = msg.chat.id;
  const opts = {
    reply_markup: {
      inline_keyboard: [
        [
          { text: `📅 Buy Daily 5 Odds (KES 500)`, callback_data: 'plan_daily' },
        ],
        [
          { text: `💎 Join VIP Elite (KES 3,000)`, callback_data: 'plan_vip' }
        ]
      ]
    }
  };
  bot.sendMessage(chatId, "Welcome to PRIME TIPS! ⚽\nSelect a subscription plan below to get access instantly:", opts);
});

// Handle Button Selections
bot.on('callback_query', (callbackQuery) => {
  const chatId = callbackQuery.message.chat.id;
  const planKey = callbackQuery.data.replace('plan_', '');

  if (PLANS[planKey]) {
    userStates[chatId] = { chosenPlan: planKey };
    bot.sendMessage(chatId, `You chose: *${PLANS[planKey].name}* (KES ${PLANS[planKey].amount})\n\nPlease reply with your M-Pesa phone number in the format *07XXXXXXXX* or *2547XXXXXXXX*:`, { parse_mode: 'Markdown' });
  }
});

// Capture User Reply (Phone Number)
bot.on('message', async (msg) => {
  const chatId = msg.chat.id;
  
  // Ignore command messages
  if (msg.text && msg.text.startsWith('/')) return;

  // Check if we are waiting for this user's phone number
  if (userStates[chatId] && userStates[chatId].chosenPlan && !userStates[chatId].phone) {
    const phoneInput = msg.text.trim();
    const cleanPhone = normalizePhone(phoneInput);

    if (!/^254(7|1)\d{8}$/.test(cleanPhone)) {
      bot.sendMessage(chatId, "❌ Invalid Safaricom number. Please enter a valid number (e.g. 0712345678):");
      return;
    }

    const planKey = userStates[chatId].chosenPlan;
    const amount = PLANS[planKey].amount;

    bot.sendMessage(chatId, `⏳ Initiating payment of KES ${amount} to ${phoneInput}...\nPlease check your phone for the M-Pesa PIN prompt.`);

    // Trigger PayHero STK Push
    const success = await triggerPayHeroSTK(cleanPhone, amount, chatId, planKey);

    if (success) {
      bot.sendMessage(chatId, "🔐 STK Push sent! Enter your PIN on your phone. Once payment is confirmed, your tips or access link will be delivered here instantly.");
    } else {
      bot.sendMessage(chatId, "❌ Payment initiation failed. Please try again or contact the admin @Isla_Olivia0.");
    }
    
    // Clear user state so they can buy again later
    delete userStates[chatId];
  }
});

/**
 * Trigger STK push through PayHero API
 */
async function triggerPayHeroSTK(phone, amount, chatId, planKey) {
  // We save ChatID and Plan inside the external_reference to decode it when callback arrives
  const reference = `PT_${chatId}_${planKey}_${Date.now()}`;

  const payload = {
    amount: amount,
    phone_number: phone,
    channel_id: parseInt(process.env.PAYHERO_CHANNEL_ID, 10),
    provider: "m-pesa",
    network_code: "63902",
    external_reference: reference,
    customer_name: "Telegram User",
    callback_url: process.env.PAYHERO_CALLBACK_URL
  };

  try {
    const auth = process.env.PAYHERO_AUTH_TOKEN.startsWith('Basic ') 
      ? process.env.PAYHERO_AUTH_TOKEN 
      : `Basic ${process.env.PAYHERO_AUTH_TOKEN}`;

    const res = await axios.post('https://backend.payhero.co.ke/api/v2/payments', payload, {
      headers: {
        'Content-Type': 'application/json',
        'Authorization': auth
      }
    });

    return res.status === 201 || res.data.success;
  } catch (error) {
    console.error("PayHero STK Error:", error.response ? error.response.data : error.message);
    return false;
  }
}

/**
 * PAYHERO CALLBACK WEBHOOK
 * PayHero hits this endpoint automatically when payment is success/fail
 */
app.post('/payhero-callback', (req, res) => {
  const payload = req.body;
  
  // PayHero sends flat object or nested "response" depending on API version
  const status = payload.status || (payload.response && payload.response.status);
  const externalRef = payload.external_reference || (payload.response && payload.response.external_reference);
  const amount = payload.amount || (payload.response && payload.response.amount);

  console.log(`Payment Callback Received. Ref: ${externalRef} | Status: ${status}`);

  if (status === 'Success' && externalRef) {
    // Decipher reference format: PT_CHATID_PLAN_TIMESTAMP
    const parts = externalRef.split('_');
    if (parts.length >= 3) {
      const chatId = parts[1];
      const planKey = parts[2];

      const plan = PLANS[planKey];
      if (plan) {
        // Send success notification to customer
        bot.sendMessage(chatId, `🎉 *Payment Confirmed!* KES ${amount} received successfully.\n\nThank you for subscribing to *${plan.name}*. Here is your access:`, { parse_mode: 'Markdown' });
        
        if (plan.tips) {
          // If the plan is "Daily Tips", send tips text directly
          bot.sendMessage(chatId, plan.tips);
        } else if (plan.inviteLink) {
          // If the plan is a "VIP Group", send them their invite link
          bot.sendMessage(chatId, `👉 [Click Here to Join Private Channel](${plan.inviteLink})`, { parse_mode: 'Markdown' });
        }
      }
    }
  }

  res.sendStatus(200); // Always reply 200 OK to PayHero
});

// Normalize phone numbers to Kenyan prefix formats (2547...)
function normalizePhone(p) {
  p = String(p).replace(/\D/g, '');
  if (p.startsWith('254')) return p;
  if (p.startsWith('0')) return '254' + p.substring(1);
  if (p.length === 9) return '254' + p;
  return p;
}

// Start API Server
const PORT = process.env.PORT || 3000;
app.listen(PORT, () => {
  console.log(`Express callback server listening on port ${PORT}`);
});
