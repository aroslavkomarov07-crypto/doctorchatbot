from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()
    phone = State()
    confirm = State()


class ExpertApplicationForm(StatesGroup):
    specialization = State()
    short_bio = State()
    bio = State()
    photo = State()
    confirm = State()


class ClientQuestion(StatesGroup):
    text = State()
    confirm = State()


class ExpertAnswer(StatesGroup):
    text = State()
    confirm = State()


class WithdrawalForm(StatesGroup):
    bank = State()
    confirm = State()


class ExpertPriceForm(StatesGroup):
    price = State()
    confirm = State()


class ExpertProfileEdit(StatesGroup):
    value = State()
    confirm = State()


class OwnerCommissionForm(StatesGroup):
    service = State()
    confirm = State()


class OwnerTariffForm(StatesGroup):
    name = State()
    duration = State()
    limit = State()
    price = State()
    confirm = State()


class OwnerTariffEdit(StatesGroup):
    name = State()
    price = State()
    duration = State()
    confirm = State()


class OwnerBroadcastForm(StatesGroup):
    text = State()
    audience = State()
    confirm = State()


class OwnerSearch(StatesGroup):
    query = State()


class OwnerUserMessage(StatesGroup):
    text = State()
    confirm = State()


class OwnerExpertTariffPrice(StatesGroup):
    price = State()
    confirm = State()


class OwnerReferralForm(StatesGroup):
    value = State()
    confirm = State()
