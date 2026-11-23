
from app import app
from db_models import db, User, SupportConversation, SupportMessage

with app.app_context():

    try:
        db.create_all()

        print("Database tables are available.")

        customer = User.query.filter_by(
            role="customer"
        ).first()

        if customer:

            conversations = SupportConversation.query.filter_by(
                customer_id=customer.id
            ).all()

            print("Customer:", customer.username)
            print("Conversations:", len(conversations))

            for conversation in conversations:

                print("\nConversation:", conversation.subject)
                print("Status:", conversation.status)

                for message in conversation.messages:

                    print(
                        message.sender.username,
                        ":",
                        message.message
                    )

        else:
            print("No customer account found yet.")

        print("\nSupport relationship test completed.")

    except Exception as error:

        db.session.rollback()

        print("ERROR:", error)
