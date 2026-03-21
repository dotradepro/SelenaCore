const uk = {
    translation: {
        // ── Common ──
        common: {
            loading: 'Завантаження...',
            error: 'Помилка',
            save: 'Зберегти',
            cancel: 'Скасувати',
            delete: 'Видалити',
            back: 'Назад',
            next: 'Далі',
            skip: 'Пропустити',
            finish: 'Завершити',
            search: 'Пошук',
            refresh: 'Оновити',
            all: 'Усі',
            on: 'Увімк',
            off: 'Вимк',
            active: 'Активний',
            yes: 'Так',
            no: 'Ні',
            required: 'обов\'язково',
            inDevelopment: 'У розробці (v0.3-beta)',
            noData: 'немає даних',
            never: 'Ніколи',
            secondsAgo: '{{count}}с тому',
            minutesAgo: '{{count}}хв тому',
            hoursAgo: '{{count}}год тому',
            daysAgo: '{{count}}д тому',
            days: '{{count}}д',
            hours: '{{count}}год',
            minutes: '{{count}}хв',
            systemActive: 'Система активна',
            listening: 'Слухаю',
        },

        // ── Navigation ──
        nav: {
            dashboard: 'Дашборд',
            devices: 'Пристрої',
            modules: 'Модулі',
            settings: 'Налаштування',
        },

        // ── Dashboard ──
        dashboard: {
            welcomeHome: 'Ласкаво просимо додому',
            safeModeWarning: '⚠ Система в безпечному режимі',
            allSystemsNormal: 'Усі системи працюють у штатному режимі.',
            systemCore: 'Ядро системи',
            cpuTemp: 'CPU Temp',
            ram: 'RAM',
            disk: 'Диск',
            uptime: 'Uptime',
            integrity: 'Integrity',
            quickActions: 'Швидкі дії',
            noActuators: 'Немає пристроїв типу actuator / virtual.',
            addDevicesViaApi: 'Додайте пристрої через Core API.',
            deviceCount: 'Пристроїв',
            moduleCount: 'Модулів',
            activeCount: 'Активних',
            activeModules: 'Активні модулі',
            noModulesInstalled: 'Немає встановлених модулів.',
            turnedOn: 'Увімкнено',
            turnedOff: 'Вимкнено',
        },

        // ── Devices ──
        devices: {
            title: 'Пристрої',
            registryInfo: 'Device Registry — {{count}} пристроїв зареєстровано.',
            searchPlaceholder: 'Пошук за назвою або протоколом...',
            noDevicesRegistered: 'Немає зареєстрованих пристроїв. Додайте пристрої через Core API.',
            noFilterResults: 'Нічого не знайдено за фільтром.',
            sensor: 'Сенсор',
            actuator: 'Виконавець',
            controller: 'Контролер',
            virtual: 'Віртуальний',
        },

        // ── Modules ──
        modules: {
            title: 'Модулі',
            subtitle: 'Керування плагінами та інтеграціями (Plugin Manager).',
            marketplace: 'Маркетплейс',
            searchPlaceholder: 'Пошук модулів...',
            noModulesInstalled: 'Немає встановлених модулів.',
            running: 'Працює',
            stop: 'Зупинити',
            start: 'Запустити',
            systemModuleCannotDelete: 'Системний модуль не можна видалити',
        },

        // ── Settings ──
        settings: {
            title: 'Налаштування',
            voiceAndLlm: 'Голос і LLM',
            audio: 'Аудіо',
            networkAndVpn: 'Мережа і VPN',
            users: 'Користувачі',
            system: 'Система',
            security: 'Безпека',

            // Voice
            voiceAssistant: 'Голосовий асистент',
            voiceAssistantDesc: 'Налаштування розпізнавання мовлення (STT) та синтезу (TTS).',
            wakeWord: 'Wake-word (openWakeWord)',
            wakeWordLabel: 'Слово пробудження',
            wakeWordDesc: 'Активує запис мікрофона',
            llmRouter: 'LLM Intent Router',
            localLlm: 'Локальна LLM (Ollama)',
            localLlmDesc: 'Використовується для складних команд (Рівень 2)',
            llmActive: 'Активно',

            // STT / TTS
            sttModel: 'Модель STT (Whisper)',
            ttsVoice: 'Голос TTS (Piper)',
            installed: 'Встановлено',
            playing: 'Відтворення…',
            preview: 'Прослухати',
            llmUnavailable: 'Недоступний',
            available: 'доступно',
            ramRequired: 'Потрібно RAM',
            activate: 'Активувати',
            download: 'Завантажити',
            downloading: 'Завантаження…',

            // Audio
            audioSubsystem: 'Аудіо-підсистема',
            audioSubsystemDesc: 'Налаштування мікрофонів та динаміків.',
            microphone: 'Мікрофон',
            speaker: 'Динамік',
            noDevicesFound: 'Пристрої не знайдено',
            testMic: 'Тест мікрофона',

            // Network
            networkTitle: 'Мережа',
            networkDesc: 'Wi-Fi, Ethernet та підключення до інтернету.',
            networkStatus: 'Стан підключення',
            internet: 'Інтернет',
            connected: 'Підключено',
            disconnected: 'Відключено',
            wifiNetworks: 'Мережі Wi-Fi',
            scan: 'Сканувати',
            clickScan: 'Натисніть Сканувати для пошуку мереж',
            wifiPassword: 'Пароль',
            connect: 'Підключити',
            connecting: 'Підключення…',
            nmcliNotAvailable: 'Керування Wi-Fi (nmcli) недоступне на цій системі.',

            // System
            systemTitle: 'Система',
            systemDesc: 'Моніторинг ресурсів та деградація.',
            degradationStrategy: 'Стратегія деградації',
            autoStopAutomation: 'Автозупинка AUTOMATION при RAM < 150 MB',
            stopLlmOnHighTemp: 'Зупинити LLM Engine при CPU > 90°C',
        },

        // ── Wizard ──
        wizard: {
            coreTitle: 'SmartHome LK Core',
            initialSetup: 'Початкове налаштування системи',

            // Steps
            stepLanguage: 'Мова',
            stepWifi: 'Wi-Fi',
            stepHomeName: 'Назва дому',
            stepTimezone: 'Часовий пояс',
            stepStt: 'STT Модель',
            stepTts: 'TTS Голос',
            stepUser: 'Користувач',
            stepPlatform: 'Платформа',
            stepImport: 'Імпорт',

            // Step 1 - Language
            selectLanguage: 'Оберіть мову',
            languageDesc: 'Мова інтерфейсу та голосового асистента.',

            // Step 2 - Wi-Fi
            wifiTitle: 'Підключення до Wi-Fi',
            wifiDesc: 'Оберіть мережу для підключення Raspberry Pi до інтернету.',
            wifiPassword: 'Пароль мережі',
            wifiPasswordPlaceholder: 'Пароль Wi-Fi',
            wifiScanning: 'Сканування мереж…',
            wifiNotAvailable: 'Керування Wi-Fi недоступне.',
            wifiNoNetworks: 'Мережі не знайдено. Оновіть.',
            wifiConnected: 'Підключено',

            // Step 3 - Device name
            deviceNameTitle: 'Назва пристрою',
            deviceNameDesc: 'Як називатиметься цей хаб? Ця назва використовується на платформі та у голосових відповідях.',
            deviceNamePlaceholder: 'Наприклад: Розумний дім — кухня',
            defaultHomeName: 'Розумний дім',

            // Step 4 - Timezone
            timezoneTitle: 'Часовий пояс',
            timezoneDesc: 'Необхідний для коректної роботи автоматизацій за часом.',
            timezoneSearch: 'Пошук часового поясу…',
            timezoneSelected: 'Обрано',

            // Step 5 - STT
            sttTitle: 'Голосова модель STT (Whisper)',
            sttDesc: 'Оберіть модель розпізнавання мовлення. Працює повністю локально.',
            sttTiny: 'Tiny',
            sttTinyDesc: 'Найшвидша. Рекомендовано для Pi 4.',
            sttBase: 'Base',
            sttBaseDesc: 'Оптимальний баланс швидкості та якості.',
            sttSmall: 'Small',
            sttSmallDesc: 'Висока якість. Тільки для Pi 5.',
            sttInstalled: 'Встановлено',
            sttNoRam: 'Недостатньо RAM',
            sttAvailable: 'доступно',
            sttTotal: 'всього',

            // Step 6 - TTS
            ttsTitle: 'Голос асистента (Piper TTS)',
            ttsDesc: 'Оберіть голос для відповідей. Модель буде завантажена (~50 MB).',
            ttsIrina: 'Ірина (Жіночий)',
            ttsDmitry: 'Дмитро (Чоловічий)',
            ttsRuslan: 'Руслан (Чоловічий)',
            ttsKseniya: 'Ксенія (Жіночий)',
            ttsFemale: 'Жіночий',
            ttsMale: 'Чоловічий',
            ttsPreview: 'Прослухати',
            ttsPlaying: 'Відтворення…',

            // Step 7 - User
            userTitle: 'Перший користувач (Admin)',
            userDesc: 'Створіть профіль адміністратора. PIN-код потрібен для доступу до налаштувань.',
            userName: 'Ім\'я',
            userPin: 'PIN-код (4-8 цифр)',
            userPinPlaceholder: '••••',

            // Step 8 - Platform
            platformTitle: 'Реєстрація на платформі',
            platformDesc: 'Підключіть хаб до хмари SmartHome LK для віддаленого доступу та маркетплейсу модулів. Можна пропустити.',
            platformQrHint: 'Відскануйте QR-код через додаток\nабо натисніть "Пропустити"',

            // Step 9 - Import
            importTitle: 'Імпорт пристроїв',
            importDesc: 'У вас вже є розумний дім? Імпортуйте пристрої з інших систем.',
            importHa: 'Home Assistant',
            importTuya: 'Tuya / SmartLife',
            importHue: 'Philips Hue',
            importMqtt: 'MQTT Broker',
            importLocal: 'Локально',
            importCloud: 'Хмара',

            unknownError: 'Невідома помилка',
        },

        // ── Language Select ──
        languageSelect: {
            scanForSetup: 'Відскануйте для налаштування з телефону',
            tapToContinue: 'Натисніть на екран для продовження налаштування',
        },

        // ── Setup Landing ──
        setupLanding: {
            mobileSetup: 'Мобільне налаштування',
            scanForSetup: 'Відскануйте для налаштування',
            qrUnavailable: 'QR недоступний',
            selenaCore: 'SelenaCore',
            continueSetup: 'Продовжити налаштування\nна пристрої',
            setupDescription: 'Використовуйте майстер налаштування тут або відскануйте QR-код зі смартфону.',
            setupStatus: 'Статус налаштування',
            checking: 'Перевірка…',
            setupHere: 'Налаштувати тут',
            goToDashboard: 'Перейти до головного меню',
            requiredStepsIncomplete: 'Не всі обов\'язкові кроки виконані. Завершіть налаштування для продовження.',
        },

        // ── Wake word options ──
        wakeWords: {
            home: 'Дім',
            alice: 'Аліса (mock)',
            computer: 'Комп\'ютер',
        },
    },
} as const;

export default uk;
