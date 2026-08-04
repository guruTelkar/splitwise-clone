from datetime import datetime

from pydantic import BaseModel, EmailStr, Field, field_validator, model_validator


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenResponse(BaseModel):
    token: str
    user: dict


class UserUpdate(BaseModel):
    name: str | None = None
    base_currency: str | None = None
    avatar_url: str | None = None


class AddFriendRequest(BaseModel):
    friend_id: int | None = None
    email: EmailStr | None = None
    mobile: str | None = None
    name: str | None = Field(default=None, min_length=1, max_length=120)

    @model_validator(mode="after")
    def check_source(self):
        if self.friend_id is None and self.email is None and self.mobile is None:
            raise ValueError("Provide either friend_id, email, or mobile")
        return self


class CreateGroupRequest(BaseModel):
    name: str = Field(min_length=1, max_length=160)
    description: str | None = None
    group_type: str = "Other"
    currency: str = "USD"
    simplify_debts: bool = True
    member_ids: list[int] = []


class UpdateGroupRequest(BaseModel):
    name: str | None = None
    description: str | None = None
    group_type: str | None = None
    currency: str | None = None
    simplify_debts: bool | None = None
    is_archived: bool | None = None


class AddMemberRequest(BaseModel):
    user_id: int


class JoinGroupRequest(BaseModel):
    code: str = Field(min_length=3, max_length=40)


class PayerIn(BaseModel):
    user_id: int
    amount: float = Field(gt=0)


class ParticipantIn(BaseModel):
    user_id: int
    share: float = Field(ge=0)
    weight: float = 1.0


class CreateExpenseRequest(BaseModel):
    group_id: int | None = None
    description: str = Field(min_length=1, max_length=300)
    amount: float = Field(gt=0)
    currency: str = "USD"
    date: str | None = None
    category: str = "General"
    notes: str | None = None
    receipt_url: str | None = None
    location: str | None = None
    split_method: str = "equally"  # equally | amounts | percentages | shares | adjustment
    payers: list[PayerIn]
    participants: list[int]  # user_ids taking part in the split
    shares: list[float] | None = None  # per participant for amounts/percentages/shares
    weights: list[float] | None = None

    @field_validator("split_method")
    @classmethod
    def validate_split_method(cls, v: str) -> str:
        allowed = {"equally", "amounts", "percentages", "shares", "adjustment"}
        if v not in allowed:
            raise ValueError(f"split_method must be one of {sorted(allowed)}")
        return v


class CreatePaymentRequest(BaseModel):
    from_user_id: int
    to_user_id: int
    amount: float = Field(gt=0)
    currency: str = "USD"
    note: str | None = None


class CreateCommentRequest(BaseModel):
    text: str = Field(min_length=1, max_length=1000)


class CreateRecurringRequest(BaseModel):
    group_id: int | None = None
    description: str = Field(min_length=1, max_length=300)
    amount: float = Field(gt=0)
    currency: str = "USD"
    category: str = "General"
    frequency: str = Field(pattern="^(daily|weekly|monthly|yearly)$")
    interval: int = 1
    start_date: str | None = None
    end_date: str | None = None
    split_method: str = "equally"
    participants: list[int]
    shares: list[float] | None = None
    weights: list[float] | None = None


class CreateReminderRequest(BaseModel):
    expense_id: int
    to_user_id: int
    remind_date: str
    message: str | None = None


class CreatePackingItemRequest(BaseModel):
    name: str = Field(min_length=1, max_length=200)
    assigned_to: int | None = None


class ProRequest(BaseModel):
    enable: bool = True


class RegisterRequest(BaseModel):
    email: EmailStr
    name: str = Field(min_length=1, max_length=120)
    password: str = Field(min_length=6, max_length=128)
    mobile: str = Field(min_length=8, max_length=20)


class SendOtpRequest(BaseModel):
    email: EmailStr | None = None
    mobile: str | None = None
    purpose: str = "register"  # register | forgot_password | forgot_userid | verify_email


class VerifyOtpRequest(BaseModel):
    email: EmailStr | None = None
    mobile: str | None = None
    code: str = Field(min_length=4, max_length=8)
    purpose: str = "register"


class ForgotPasswordRequest(BaseModel):
    email: EmailStr | None = None
    mobile: str | None = None


class ResetPasswordRequest(BaseModel):
    email: EmailStr
    code: str
    new_password: str = Field(min_length=6, max_length=128)


class ForgotUserIdRequest(BaseModel):
    email: EmailStr | None = None
    mobile: str | None = None
