from aiogram.fsm.state import State, StatesGroup


class Registration(StatesGroup):
    full_name = State()
    phone = State()
    review = State()


class ExpertForm(StatesGroup):
    full_name = State()
    specialization = State()
    education = State()
    experience = State()
    description = State()
    photo = State()
    review = State()


class Compose(StatesGroup):
    text = State()
    review = State()


class TariffForm(StatesGroup):
    name = State()
    description = State()
    days = State()
    limit = State()
    price = State()
    review = State()


class ValueForm(StatesGroup):
    value = State()
    review = State()


class WithdrawalForm(StatesGroup):
    amount = State()
    bank = State()
    review = State()
