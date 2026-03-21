const en = {
    translation: {
        // ── Common ──
        common: {
            loading: 'Loading...',
            error: 'Error',
            save: 'Save',
            cancel: 'Cancel',
            delete: 'Delete',
            back: 'Back',
            next: 'Next',
            skip: 'Skip',
            finish: 'Finish',
            search: 'Search',
            refresh: 'Refresh',
            all: 'All',
            on: 'On',
            off: 'Off',
            active: 'Active',
            yes: 'Yes',
            no: 'No',
            required: 'required',
            inDevelopment: 'In development (v0.3-beta)',
            noData: 'no data',
            never: 'Never',
            secondsAgo: '{{count}}s ago',
            minutesAgo: '{{count}}m ago',
            hoursAgo: '{{count}}h ago',
            daysAgo: '{{count}}d ago',
            days: '{{count}}d',
            hours: '{{count}}h',
            minutes: '{{count}}m',
            systemActive: 'System active',
            listening: 'Listening',
        },

        // ── Navigation ──
        nav: {
            dashboard: 'Dashboard',
            devices: 'Devices',
            modules: 'Modules',
            settings: 'Settings',
        },

        // ── Dashboard ──
        dashboard: {
            welcomeHome: 'Welcome home',
            safeModeWarning: '⚠ System is in safe mode',
            allSystemsNormal: 'All systems operating normally.',
            systemCore: 'System Core',
            cpuTemp: 'CPU Temp',
            ram: 'RAM',
            disk: 'Disk',
            uptime: 'Uptime',
            integrity: 'Integrity',
            quickActions: 'Quick actions',
            noActuators: 'No actuator / virtual devices.',
            addDevicesViaApi: 'Add devices via Core API.',
            deviceCount: 'Devices',
            moduleCount: 'Modules',
            activeCount: 'Active',
            activeModules: 'Active modules',
            noModulesInstalled: 'No modules installed.',
            turnedOn: 'On',
            turnedOff: 'Off',
        },

        // ── Devices ──
        devices: {
            title: 'Devices',
            registryInfo: 'Device Registry — {{count}} devices registered.',
            searchPlaceholder: 'Search by name or protocol...',
            noDevicesRegistered: 'No registered devices. Add devices via Core API.',
            noFilterResults: 'No results matching filter.',
            sensor: 'Sensor',
            actuator: 'Actuator',
            controller: 'Controller',
            virtual: 'Virtual',
        },

        // ── Modules ──
        modules: {
            title: 'Modules',
            subtitle: 'Manage plugins and integrations (Plugin Manager).',
            marketplace: 'Marketplace',
            searchPlaceholder: 'Search modules...',
            noModulesInstalled: 'No modules installed.',
            running: 'Running',
            stop: 'Stop',
            start: 'Start',
            systemModuleCannotDelete: 'System module cannot be deleted',
        },

        // ── Settings ──
        settings: {
            title: 'Settings',
            voiceAndLlm: 'Voice & LLM',
            audio: 'Audio',
            networkAndVpn: 'Network & VPN',
            users: 'Users',
            system: 'System',
            security: 'Security',

            // Voice
            voiceAssistant: 'Voice assistant',
            voiceAssistantDesc: 'Configure speech recognition (STT) and synthesis (TTS).',
            wakeWord: 'Wake-word (openWakeWord)',
            wakeWordLabel: 'Wake word',
            wakeWordDesc: 'Activates microphone recording',
            llmRouter: 'LLM Intent Router',
            localLlm: 'Local LLM (Ollama)',
            localLlmDesc: 'Used for complex commands (Level 2)',
            llmActive: 'Active',

            // STT / TTS
            sttModel: 'STT Model (Whisper)',
            ttsVoice: 'TTS Voice (Piper)',
            installed: 'Installed',
            playing: 'Playing…',
            preview: 'Preview',
            llmUnavailable: 'Unavailable',
            available: 'available',
            ramRequired: 'RAM required',
            activate: 'Activate',
            download: 'Download',
            downloading: 'Downloading…',

            // Audio
            audioSubsystem: 'Audio subsystem',
            audioSubsystemDesc: 'Configure microphones and speakers.',
            microphone: 'Microphone',
            speaker: 'Speaker',
            noDevicesFound: 'No devices found',
            testMic: 'Test microphone',

            // Network
            networkTitle: 'Network',
            networkDesc: 'Wi-Fi, Ethernet, and internet connectivity.',
            networkStatus: 'Connection status',
            internet: 'Internet',
            connected: 'Connected',
            disconnected: 'Disconnected',
            wifiNetworks: 'Wi-Fi networks',
            scan: 'Scan',
            clickScan: 'Click Scan to search for networks',
            wifiPassword: 'Password',
            connect: 'Connect',
            connecting: 'Connecting…',
            nmcliNotAvailable: 'WiFi management (nmcli) is not available on this system.',

            // System
            systemTitle: 'System',
            systemDesc: 'Resource monitoring and degradation.',
            degradationStrategy: 'Degradation strategy',
            autoStopAutomation: 'Auto-stop AUTOMATION when RAM < 150 MB',
            stopLlmOnHighTemp: 'Stop LLM Engine when CPU > 90°C',
            resetWizardTitle: 'Initial setup',
            resetWizardDesc: 'Reset the setup wizard to re-run the initial configuration from scratch.',
            resetWizardBtn: 'Reset setup wizard',
            resetWizardConfirm: 'Reset the setup wizard? The page will reload and you will start from step 1.',
        },

        // ── Wizard ──
        wizard: {
            coreTitle: 'SmartHome LK Core',
            initialSetup: 'Initial system setup',

            // Steps
            stepLanguage: 'Language',
            stepWifi: 'Wi-Fi',
            stepHomeName: 'Home name',
            stepTimezone: 'Timezone',
            stepStt: 'STT Model',
            stepTts: 'TTS Voice',
            stepUser: 'User',
            stepPlatform: 'Platform',
            stepImport: 'Import',

            // Step 1 - Language
            selectLanguage: 'Select language',
            languageDesc: 'Interface and voice assistant language.',

            // Step 2 - Wi-Fi
            wifiTitle: 'Internet connection',
            wifiDesc: 'Connect with Ethernet cable or Wi-Fi network.',
            wifiAdapter: 'Wi-Fi',
            wifiAdapterOn: 'Adapter enabled — scanning for networks',
            wifiAdapterOff: 'Adapter disabled',
            wifiPassword: 'Network password',
            wifiPasswordPlaceholder: 'Wi-Fi password',
            wifiScanning: 'Scanning networks…',
            wifiNotAvailable: 'No Wi-Fi adapter detected and no Ethernet connected.',
            wifiNoNetworks: 'No networks found. Pull to refresh.',
            wifiConnected: 'Connected',
            ethernetConnected: 'Ethernet connected',
            ethernetSkipHint: 'Internet available via cable. You can skip Wi-Fi setup or configure it as backup.',

            // Step 3 - Device name
            deviceNameTitle: 'Device name',
            deviceNameDesc: 'What will this hub be called? This name is used on the platform and in voice responses.',
            deviceNamePlaceholder: 'For example: Smart home — kitchen',
            defaultHomeName: 'Smart Home',

            // Step 4 - Timezone
            timezoneTitle: 'Timezone',
            timezoneDesc: 'Required for time-based automations to work correctly.',
            timezoneSearch: 'Search timezone…',
            timezoneSelected: 'Selected',

            // Step 5 - STT
            sttTitle: 'STT Voice Model (Whisper)',
            sttDesc: 'Select a speech recognition model. Works fully locally.',
            sttTiny: 'Tiny',
            sttTinyDesc: 'Fastest. Recommended for Pi 4.',
            sttBase: 'Base',
            sttBaseDesc: 'Optimal balance of speed and quality.',
            sttSmall: 'Small',
            sttSmallDesc: 'High quality. Pi 5 only.',
            sttInstalled: 'Installed',
            sttNoRam: 'Not enough RAM',
            sttAvailable: 'available',
            sttTotal: 'total',

            // Step 6 - TTS
            ttsTitle: 'Assistant voice (Piper TTS)',
            ttsDesc: 'Select a voice for responses. Model will be downloaded (~50 MB).',
            ttsIrina: 'Irina (Female)',
            ttsDmitry: 'Dmitry (Male)',
            ttsRuslan: 'Ruslan (Male)',
            ttsKseniya: 'Kseniya (Female)',
            ttsFemale: 'Female',
            ttsMale: 'Male',
            ttsPreview: 'Preview',
            ttsPlaying: 'Playing…',

            // Step 7 - User
            userTitle: 'First user (Admin)',
            userDesc: 'Create an administrator profile. PIN is required for settings access.',
            userName: 'Name',
            userPin: 'PIN code (4-8 digits)',
            userPinPlaceholder: '••••',

            // Step 8 - Platform
            platformTitle: 'Platform registration',
            platformDesc: 'Connect the hub to SmartHome LK cloud for remote access and module marketplace. Can be skipped.',
            platformQrHint: 'Scan the QR code via the app\nor click "Skip"',

            // Step 9 - Import
            importTitle: 'Import devices',
            importDesc: 'Already have a smart home? Import devices from other systems.',
            importHa: 'Home Assistant',
            importTuya: 'Tuya / SmartLife',
            importHue: 'Philips Hue',
            importMqtt: 'MQTT Broker',
            importLocal: 'Local',
            importCloud: 'Cloud',

            unknownError: 'Unknown error',
        },

        // ── Language Select ──
        languageSelect: {
            scanForSetup: 'Scan to set up from your phone',
            tapToContinue: 'Tap the screen to continue setup',
        },

        // ── Setup Landing ──
        setupLanding: {
            mobileSetup: 'Mobile setup',
            scanForSetup: 'Scan to set up',
            qrUnavailable: 'QR unavailable',
            selenaCore: 'SelenaCore',
            continueSetup: 'Continue setup\non device',
            setupDescription: 'Use the setup wizard here or scan the QR code from your smartphone.',
            setupStatus: 'Setup status',
            checking: 'Checking…',
            setupHere: 'Set up here',
            goToDashboard: 'Go to dashboard',
            requiredStepsIncomplete: 'Not all required steps are completed. Finish setup to continue.',
        },

        // ── Wake word options ──
        wakeWords: {
            home: 'Home',
            alice: 'Alice (mock)',
            computer: 'Computer',
        },
    },
} as const;

export default en;
