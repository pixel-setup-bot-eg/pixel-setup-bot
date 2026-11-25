# بوت سيرفر Pixel: دليل التشغيل (بتعمله إنت بنفسك)

البوت بيعمل حاجتين: أمر `/setup` يبني الرتب والقنوات والصلاحيات، وحماية تلقائية (لينكات، سبام، صور كتير، نصب) مع لوج.
**مهم:** التوكن (Token) زي الباسورد. ما تبعتوش لأي حد في شات ولا تحطه في الكود. بيتحط بس في إعدادات الاستضافة (Environment Variable).

## 1) اعمل حسابات (بإيميلك وإنت اللي بتأكد الأكواد)
1. حساب GitHub: https://github.com/signup
2. حساب استضافة: https://bot-hosting.net (مجاني، من غير كارت، بس لازم تجدد كل 4 أيام من الداشبورد)

## 2) ارفع ملفات البوت على GitHub
1. على GitHub اعمل Repository جديد اسمه `pixel-setup-bot` (Public أو Private).
2. دوس Add file > Upload files وارفع الملفات: `bot.py` و `config.json` و `requirements.txt`.
3. دوس Commit changes.
(لو الاستضافة بتقبل رفع ملف zip، ممكن ترفع الزيب مباشرة بدل GitHub.)

## 3) اعمل البوت في Discord
1. افتح https://discord.com/developers/applications > New Application > سمّيه (مثلاً Pixel Setup).
2. من الشمال **Bot**. اضغط **Reset Token** وانسخ التوكن (هتحتاجه في خطوة 4 بس).
3. لسه في صفحة Bot، انزل لـ **Privileged Gateway Intents** وفعّل:
   - **Server Members Intent**
   - **Message Content Intent**
   ثم Save Changes.
4. من General Information انسخ **Application ID** (ده مش سري، عادي تبعته).

## 4) شغّل البوت على الاستضافة
1. في bot-hosting.net: New deployment > اختار GitHub وربط الـ repo (أو Archive وارفع zip).
2. Start command: `python bot.py` (ولو طلب Install: `pip install -r requirements.txt`).
3. في Environment Variables ضيف: الاسم `DISCORD_TOKEN` والقيمة التوكن بتاعك.
4. Start. في الـ Console لازم تلاقي سطر `Pixel setup bot online`.
   ولو ظهر خطأ intents يبقى رجّع لخطوة 3.3.

## 5) ادعي البوت (جرّب في سيرفر فاضي الأول)
1. اعمل سيرفر تجريبي فاضي.
2. افتح اللينك ده وحط الـ Application ID مكان `APPLICATION_ID`:
   `https://discord.com/oauth2/authorize?client_id=APPLICATION_ID&scope=bot%20applications.commands&permissions=8`
3. اختار السيرفر وAuthorize.

**ليه Administrator (permissions=8)؟** `/setup` بيعمل رتبة Pixel بصلاحية Administrator، والبوت مش بيعرف يدّي صلاحية هو معندوش. وكمان بيعدّل صلاحيات كل القنوات.
بعد ما `/setup` يخلص تقدر تنزّل صلاحيات البوت: من Server Settings > Roles > رتبة البوت، سيب بس: Manage Roles، Manage Channels، Manage Server، Manage Messages، Moderate Members، View Channels، Send Messages، Embed Links، Attach Files، Read Message History، Add Reactions. وشيل Administrator. (لو عايز تشغل `/setup` تاني لازم ترجّعها مؤقتاً.)

## 6) جرّب
1. في السيرفر التجريبي اكتب `/setup` (للأدمن بس).
2. اتأكد: القنوات والرتب اتعملت، رتبة Pixel عندك، رسالة الذكر أول قناة، وضغطة ❤️ في "استلم رتبتك" بتدّيك Gaming.
3. اختبر مع حساب تاني: ابعت لينك أو 4 صور ورا بعض، المفروض الرسالة تتمسح والعضو ياخد تايم آوت 3 أيام.
4. لما تتأكد، اعمل نفس خطوة 5 و `/setup` في سيرفرك الحقيقي. (ممكن تمسح السيرفر التجريبي.)

## ملاحظات
- رتبة البوت لازم تبقى أعلى من الرتب اللي بيديها (Member وغيرها). `/setup` بيحاول يرتبهم لوحده.
- قنوات تيك توك/يوتيوب/فيسبوك/انستجرام/كيك والتيكت بتتعمل بالاسم بس وقفولة، مفيش حد يكتب فيها غيرك. لما تضيف بوتاتك اديهم صلاحية الكتابة.
- أسماء القنوات والرتب والرسايل كلها في `config.json` تقدر تعدلها.
- القناة الصوتية الخاصة والـ log ظاهرين لرتبة Pixel بس. قناة Record ظاهرة لـ Pixel والـ Staff.
- لو لقيت إن التوكن اتسرّب: Reset Token من Developer Portal وغيّره في الاستضافة.
